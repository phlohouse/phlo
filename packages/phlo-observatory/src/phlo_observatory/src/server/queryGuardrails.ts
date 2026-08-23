/**
 * SQL guardrails for user-submitted queries.
 *
 * Every query is checked before it reaches Trino: exactly one statement is
 * allowed, the statement must classify as read-only, and read-only SELECT/WITH
 * queries are rewritten with a default and a hard maximum LIMIT. Classification
 * scans the statement with string literals and comments stripped, so keywords
 * hidden inside them cannot influence the verdict.
 */
export type QueryGuardrails = {
  readOnlyMode: boolean
  defaultLimit: number
  maxLimit: number
}

export type QueryExecutionError = {
  ok: false
  error: string
  kind: 'blocked' | 'confirm_required' | 'invalid' | 'timeout' | 'trino'
  effectiveQuery?: string
}

// Split on semicolons that sit outside single/double-quoted strings and
// comments. Doubled quotes ('' inside a quoted string) do not terminate the
// literal, so statements containing escaped quotes stay intact.
export function splitSqlStatements(sql: string): Array<string> {
  const statements: Array<string> = []
  let buffer = ''
  let inSingle = false
  let inDouble = false
  let inLineComment = false
  let inBlockComment = false

  for (let i = 0; i < sql.length; i++) {
    const char = sql[i]
    const next = i + 1 < sql.length ? sql[i + 1] : ''

    if (inLineComment) {
      buffer += char
      if (char === '\n') inLineComment = false
      continue
    }

    if (inBlockComment) {
      buffer += char
      if (char === '*' && next === '/') {
        buffer += next
        i++
        inBlockComment = false
      }
      continue
    }

    if (!inSingle && !inDouble) {
      if (char === '-' && next === '-') {
        buffer += char + next
        i++
        inLineComment = true
        continue
      }
      if (char === '/' && next === '*') {
        buffer += char + next
        i++
        inBlockComment = true
        continue
      }
    }

    if (!inDouble && char === "'") {
      buffer += char
      if (inSingle && next === "'") {
        buffer += next
        i++
        continue
      }
      inSingle = !inSingle
      continue
    }

    if (!inSingle && char === '"') {
      buffer += char
      if (inDouble && next === '"') {
        buffer += next
        i++
        continue
      }
      inDouble = !inDouble
      continue
    }

    if (!inSingle && !inDouble && char === ';') {
      const trimmed = buffer.trim()
      if (trimmed) statements.push(trimmed)
      buffer = ''
      continue
    }

    buffer += char
  }

  const last = buffer.trim()
  if (last) statements.push(last)
  return statements
}

// Blank out literals and comments so keyword detection only sees structural
// SQL text. A "SELECT" that appears inside a string literal or a comment no
// longer counts as evidence of a read-only statement.
function stripSqlForInspection(sql: string): string {
  let out = ''
  let inSingle = false
  let inDouble = false
  let inLineComment = false
  let inBlockComment = false

  for (let i = 0; i < sql.length; i++) {
    const char = sql[i]
    const next = i + 1 < sql.length ? sql[i + 1] : ''

    if (inLineComment) {
      if (char === '\n') {
        inLineComment = false
        out += '\n'
      }
      continue
    }

    if (inBlockComment) {
      if (char === '*' && next === '/') {
        i++
        inBlockComment = false
      }
      continue
    }

    if (!inSingle && !inDouble) {
      if (char === '-' && next === '-') {
        inLineComment = true
        i++
        continue
      }
      if (char === '/' && next === '*') {
        inBlockComment = true
        i++
        continue
      }
    }

    if (!inDouble && char === "'") {
      if (inSingle && next === "'") {
        i++
        continue
      }
      inSingle = !inSingle
      continue
    }

    if (!inSingle && char === '"') {
      if (inDouble && next === '"') {
        i++
        continue
      }
      inDouble = !inDouble
      continue
    }

    if (inSingle || inDouble) continue
    out += char
  }

  return out
}

function firstKeyword(sql: string): string | null {
  const stripped = stripSqlForInspection(sql).trimStart()
  const match = stripped.match(/^([A-Za-z_]+)/)
  return match ? match[1].toUpperCase() : null
}

const FORBIDDEN_KEYWORD_RE =
  /\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CALL)\b/

function containsForbiddenKeyword(sql: string): string | null {
  return (
    FORBIDDEN_KEYWORD_RE.exec(stripSqlForInspection(sql).toUpperCase())?.[1] ??
    null
  )
}

function hasLimit(sql: string): boolean {
  return /\bLIMIT\b/i.test(stripSqlForInspection(sql))
}

function applyDefaultLimit(sql: string, defaultLimit: number): string {
  if (hasLimit(sql)) return sql
  return `${sql}\nLIMIT ${defaultLimit}`
}

// Wrap as a subquery so the outer LIMIT caps results even when the inner
// query already carries its own (possibly larger) LIMIT.
function enforceMaxLimit(sql: string, maxLimit: number): string {
  return `SELECT * FROM (\n${sql}\n) AS _phlo_q\nLIMIT ${maxLimit}`
}

export function validateAndRewriteQuery(params: {
  query: string
  guardrails: QueryGuardrails
  allowUnsafe: boolean
}):
  | { ok: true; effectiveQuery: string; isReadOnly: boolean }
  | QueryExecutionError {
  const { query, guardrails, allowUnsafe } = params
  const statements = splitSqlStatements(query)
  if (statements.length === 0) {
    return { ok: false, error: 'Query is empty.', kind: 'invalid' }
  }
  if (statements.length > 1) {
    return {
      ok: false,
      error: 'Multi-statement queries are not allowed.',
      kind: 'blocked',
    }
  }

  const statement = statements[0]
  const keyword = firstKeyword(statement)
  const forbidden = containsForbiddenKeyword(statement)
  const stripped = stripSqlForInspection(statement)
  const isWithSelect = /\bSELECT\b/i.test(stripped)

  let isReadOnly = false
  if (
    keyword === 'SELECT' ||
    keyword === 'SHOW' ||
    keyword === 'DESCRIBE' ||
    keyword === 'EXPLAIN'
  ) {
    isReadOnly = !forbidden
  } else if (keyword === 'WITH') {
    isReadOnly = isWithSelect && !forbidden
  }

  if (guardrails.readOnlyMode) {
    if (!isReadOnly) {
      return {
        ok: false,
        error:
          'Only SELECT/SHOW/DESCRIBE/EXPLAIN queries are allowed in read-only mode.',
        kind: 'blocked',
        effectiveQuery: statement,
      }
    }
    if (forbidden) {
      return {
        ok: false,
        error: `Blocked statement (${forbidden}) in read-only mode.`,
        kind: 'blocked',
        effectiveQuery: statement,
      }
    }
  } else if ((!isReadOnly || forbidden) && !allowUnsafe) {
    return {
      ok: false,
      error: 'This statement is not read-only. Confirm to run it.',
      kind: 'confirm_required',
      effectiveQuery: statement,
    }
  }

  let effectiveQuery = statement
  if (isReadOnly && (keyword === 'SELECT' || keyword === 'WITH')) {
    effectiveQuery = applyDefaultLimit(effectiveQuery, guardrails.defaultLimit)
    effectiveQuery = enforceMaxLimit(effectiveQuery, guardrails.maxLimit)
  }

  return { ok: true, effectiveQuery, isReadOnly }
}
