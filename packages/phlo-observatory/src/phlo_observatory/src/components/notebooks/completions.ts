/** Editor completions: SQL from the lakehouse catalog, Python from the running kernel. */
import { PostgreSQL, keywordCompletionSource } from '@codemirror/lang-sql'
import type {
  Completion,
  CompletionContext,
  CompletionResult,
  CompletionSource,
} from '@codemirror/autocomplete'
import type { CatalogObject } from '@/lib/data/api/catalog'
import { getCatalogObjects } from '@/lib/data/api/catalog'
import { completePython } from '@/lib/data/api/notebooks'
import { sqlReads } from '@/lib/notebook'

const keywords = keywordCompletionSource(PostgreSQL, true)

/** One catalog level, fetched once per page load and shared by every cell. */
const levels = new Map<string, Promise<Array<CatalogObject>>>()
let catalogName: string | null = null

function level(env: string, schema?: string, table?: string) {
  const id = [env, schema ?? '', table ?? ''].join('\u0000')
  let found = levels.get(id)
  if (!found) {
    found = getCatalogObjects({ data: { env, schema, table, offset: 0 } }).then(
      (result) => {
        if (result.state !== 'available') return []
        catalogName = result.page.catalog
        return result.page.items
      },
      () => [],
    )
    levels.set(id, found)
  }
  return found
}

function unquote(part: string) {
  return part.replace(/^"|"$/g, '').replace(/""/g, '"')
}

function option(item: CatalogObject): Completion {
  return {
    label: item.name,
    type:
      item.kind === 'column'
        ? 'property'
        : item.kind === 'schema'
          ? 'namespace'
          : 'class',
    detail: item.data_type ?? (item.kind === 'view' ? 'view' : item.kind),
  }
}

/** What the dotted name before the cursor points into: schemas, tables or columns. */
async function dotted(env: string, parts: Array<string>) {
  const known = catalogName ?? (await level(env).then(() => catalogName))
  const path = parts[0] === known ? parts.slice(1) : parts
  if (path.length === 0) return level(env)
  if (path.length === 1) return level(env, path[0])
  return level(env, path[0], path[1])
}

/** Columns of the tables this cell already reads, so `SELECT` lists complete too. */
async function readColumns(env: string, source: string) {
  const known = catalogName ?? (await level(env).then(() => catalogName))
  const tables = sqlReads(source).slice(0, 6)
  const columns = await Promise.all(
    tables.map((name) => {
      const parts = name.split('.')
      const path = parts[0] === known ? parts.slice(1) : parts
      return path.length === 2
        ? level(env, path[0], path[1])
        : Promise.resolve([])
    }),
  )
  return columns.flat()
}

export function sqlCompletion(
  env: string,
  /** Result names earlier cells bound, for DuckDB cells */
  results: () => Array<string>,
): CompletionSource {
  return async (
    context: CompletionContext,
  ): Promise<CompletionResult | null> => {
    const word = context.matchBefore(/(?:"[^"]*"|[\w$]+|\.)*$/)
    if (!word || (word.from === word.to && !context.explicit)) return null
    const text = word.text
    const parts = text.split('.')
    const typed = parts.pop() ?? ''
    const from = word.to - typed.length
    if (parts.length > 0) {
      const items = await dotted(env, parts.map(unquote))
      return { from, options: items.map(option), validFor: /^[\w$]*$/ }
    }
    const [schemas, columns, words] = await Promise.all([
      level(env),
      readColumns(env, context.state.doc.toString()),
      Promise.resolve(keywords(context)),
    ])
    const options: Array<Completion> = [
      ...results().map((name) => ({
        label: name,
        type: 'variable',
        detail: 'result',
      })),
      ...columns.map(option),
      ...schemas.map(option),
      ...(words?.options ?? []),
    ]
    return { from, options, validFor: /^[\w$]*$/ }
  }
}

export function pythonCompletion(
  env: string,
  kernel: () => string | null,
): CompletionSource {
  return async (
    context: CompletionContext,
  ): Promise<CompletionResult | null> => {
    const id = kernel()
    const word = context.matchBefore(/[\w.]*$/)
    if (!id || !word || (word.from === word.to && !context.explicit))
      return null
    try {
      const result = await completePython({
        data: {
          env,
          kernel: id,
          code: context.state.doc.toString(),
          cursor: context.pos,
        },
      })
      if (result.state !== 'ok' || result.data.matches.length === 0) return null
      return {
        from: result.data.cursor_start,
        to: result.data.cursor_end,
        options: result.data.matches.slice(0, 200).map((label) => ({
          label,
          type: 'variable',
        })),
      }
    } catch {
      return null
    }
  }
}
