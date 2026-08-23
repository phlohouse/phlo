/**
 * SQL Parser Utilities
 *
 * Parse transformation SQL to understand column mappings and build reverse queries
 * for tracing data through the pipeline.
 */

// SQL parsing regex patterns
const PATTERNS = {
  /** Match SELECT ... FROM clause */
  SELECT_FROM: /SELECT\s+(.*?)\s+FROM/is,
  /** Match column alias with AS keyword */
  ALIAS_AS: /^(.*?)\s+AS\s+(\w+)$/i,
  /** Match column alias with space (no AS) */
  ALIAS_SPACE: /^(.*?)\s+(\w+)$/,
  /** Match single-line SQL comment */
  SINGLE_LINE_COMMENT: /--.*$/gm,
  /** Match multi-line SQL comment */
  MULTI_LINE_COMMENT: /\/\*[\s\S]*?\*\//g,
  /** Match dbt Jinja ref (global flag for exec loop) */
  DBT_REF: /\{\{\s*ref\s*\(\s*['"]([^'"]+)['"]\s*\)\s*\}\}/gi,
  /** Match dbt Jinja source (global flag for exec loop) */
  DBT_SOURCE:
    /\{\{\s*source\s*\(\s*['"][^'"]+['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*\}\}/gi,
} as const

/** SQL keywords that should not be treated as aliases */
const SQL_KEYWORDS = new Set([
  'from',
  'where',
  'group',
  'order',
  'having',
  'limit',
  'offset',
  'union',
  'intersect',
  'except',
  'join',
  'inner',
  'outer',
  'left',
  'right',
  'full',
  'cross',
  'on',
  'and',
  'or',
  'not',
  'in',
  'is',
  'null',
  'true',
  'false',
  'case',
  'when',
  'then',
  'else',
  'end',
  'as',
  'asc',
  'desc',
  'distinct',
  'all',
  'between',
  'like',
  'exists',
  'select',
  'by',
])

export interface ColumnMapping {
  targetColumn: string // Column name in the output (e.g., "activity_date")
  sourceExpression: string // Original expression (e.g., "DATE(created_at)")
  sourceColumn?: string // Base column name if extractable (e.g., "created_at")
  transformation?: string // Function applied (e.g., "DATE")
}

/**
 * Transform type classification for row lineage tracing
 */
export type TransformType =
  | 'ONE_TO_ONE' // Direct mapping, CAST, date formatting - row traceable
  | 'ONE_TO_MANY' // Window functions with PARTITION - row traceable
  | 'MANY_TO_ONE' // GROUP BY, DISTINCT, aggregates - batch only
  | 'COMPLEX' // Subqueries, CTEs, joins - limited tracing

/**
 * SQL analysis result with confidence scoring
 */
export interface SQLAnalysis {
  columnMappings: Array<ColumnMapping>
  sourceTables: Array<string>
  joinConditions?: Array<string>
  whereConditions?: Array<string>
  // Enhanced analysis fields
  transformType: TransformType
  confidence: number // 0-100%
  confidenceReasons: Array<string>
  hasCTEs: boolean
  hasWindowFunctions: boolean
  hasAggregates: boolean
  hasGroupBy: boolean
  hasSubqueries: boolean
  hasJoins: boolean
}

/**
 * Strip SQL comments to avoid matching keywords in comments
 * Handles: -- line comments and block comments
 */
function stripSqlComments(sql: string): string {
  return (
    sql
      // Remove block comments /* ... */
      .replace(PATTERNS.MULTI_LINE_COMMENT, '')
      // Remove line comments -- ...
      .replace(PATTERNS.SINGLE_LINE_COMMENT, '')
      // Clean up excessive whitespace
      .replace(/\n{2,}/g, '\n')
      .trim()
  )
}

/**
 * Parse SQL to extract column mappings from SELECT clause
 */
export function parseColumnMappings(sql: string): Array<ColumnMapping> {
  const mappings: Array<ColumnMapping> = []

  // Strip SQL comments before parsing to avoid matching SELECT/FROM in comments
  const cleanedSql = stripSqlComments(sql)

  // Extract SELECT clause (basic approach)
  const selectMatch = cleanedSql.match(PATTERNS.SELECT_FROM)
  if (!selectMatch) return mappings

  const selectClause = selectMatch[1]

  // Split by commas (handle nested functions)
  const columns = splitSelectColumns(selectClause)

  for (const col of columns) {
    const trimmed = col.trim()

    // Check for alias (AS keyword)
    const asMatch = trimmed.match(PATTERNS.ALIAS_AS)
    if (asMatch) {
      const sourceExpr = asMatch[1].trim()
      const targetCol = asMatch[2].trim()

      mappings.push({
        targetColumn: targetCol,
        sourceExpression: sourceExpr,
        sourceColumn: extractBaseColumn(sourceExpr),
        transformation: extractTransformation(sourceExpr),
      })
      continue
    }

    // Check for alias (space without AS)
    const spaceMatch = trimmed.match(PATTERNS.ALIAS_SPACE)
    if (spaceMatch && !isKeyword(spaceMatch[2])) {
      const sourceExpr = spaceMatch[1].trim()
      const targetCol = spaceMatch[2].trim()

      mappings.push({
        targetColumn: targetCol,
        sourceExpression: sourceExpr,
        sourceColumn: extractBaseColumn(sourceExpr),
        transformation: extractTransformation(sourceExpr),
      })
      continue
    }

    // No alias - column name is same as source
    const baseCol = extractBaseColumn(trimmed)
    if (baseCol) {
      mappings.push({
        targetColumn: baseCol,
        sourceExpression: trimmed,
        sourceColumn: baseCol,
      })
    }
  }

  return mappings
}

/**
 * Split SELECT columns handling nested parentheses
 */
function splitSelectColumns(selectClause: string): Array<string> {
  const columns: Array<string> = []
  let current = ''
  let depth = 0

  for (const char of selectClause) {
    if (char === '(') depth++
    if (char === ')') depth--

    if (char === ',' && depth === 0) {
      columns.push(current)
      current = ''
    } else {
      current += char
    }
  }

  if (current.trim()) {
    columns.push(current)
  }

  return columns
}

/**
 * Extract the base column name from an expression
 * Examples:
 *   "DATE(created_at)" -> "created_at"
 *   "COUNT(*)" -> null
 *   "user_id" -> "user_id"
 *   "CAST(amount AS DECIMAL)" -> "amount"
 */
function extractBaseColumn(expr: string): string | undefined {
  // Remove whitespace
  const trimmed = expr.trim()

  // Check for simple column name (no functions)
  if (/^\w+$/.test(trimmed)) {
    return trimmed
  }

  // Extract from function calls - look for column names in parentheses
  // Match patterns like FUNC(column) or FUNC(table.column)
  const funcMatch = trimmed.match(/\w+\(([\w.]+)/)
  if (funcMatch) {
    const colName = funcMatch[1]
    // If it has a dot, take the part after the dot
    if (colName.includes('.')) {
      return colName.split('.')[1]
    }
    // Skip special keywords
    if (colName !== '*' && !isKeyword(colName)) {
      return colName
    }
  }

  // Try to find any column-like identifier
  const identMatch = trimmed.match(/(\w+\.\w+|\w+)/)
  if (identMatch) {
    const ident = identMatch[1]
    if (ident.includes('.')) {
      return ident.split('.')[1]
    }
    if (!isKeyword(ident) && ident !== '*') {
      return ident
    }
  }

  return undefined
}

/**
 * Extract transformation function from expression
 */
function extractTransformation(expr: string): string | undefined {
  const funcMatch = expr.match(/^(\w+)\(/)
  return funcMatch ? funcMatch[1] : undefined
}

/**
 * Check if a word is a SQL keyword
 */
function isKeyword(word: string): boolean {
  return SQL_KEYWORDS.has(word.toLowerCase())
}

/**
 * Extract source table names from SQL
 * Handles:
 * - Quoted identifiers like "iceberg"."schema"."table"
 * - Simple unquoted table names
 * - dbt Jinja syntax: {{ ref('table') }} and {{ source('src', 'table') }}
 */
export function extractSourceTables(sql: string): Array<string> {
  const tables: Array<string> = []
  const tableSet = new Set<string>()
  const addTable = (tableName: string | undefined) => {
    if (tableName && !tableSet.has(tableName)) {
      tableSet.add(tableName)
      tables.push(tableName)
    }
  }

  // Remove EXTRACT(...FROM...) to avoid false matches
  const cleanedSql = sql.replace(/EXTRACT\s*\([^)]*FROM[^)]*\)/gi, '')

  // Use PATTERNS for dbt ref(): {{ ref('table_name') }}
  // Create new RegExp instances to reset lastIndex for global patterns
  const refPattern = new RegExp(PATTERNS.DBT_REF.source, 'gi')
  let match
  while ((match = refPattern.exec(cleanedSql)) !== null) {
    addTable(match[1])
  }

  // Use PATTERNS for dbt source(): {{ source('source_name', 'table_name') }}
  const sourcePattern = new RegExp(PATTERNS.DBT_SOURCE.source, 'gi')
  while ((match = sourcePattern.exec(cleanedSql)) !== null) {
    addTable(match[1])
  }

  // Pattern for fully quoted: "catalog"."schema"."table"
  // Captures table name (last quoted part before whitespace/newline)
  const quotedPattern = /\bFROM\s+"[^"]+"\."[^"]+"\."([^"]+)"/gi
  while ((match = quotedPattern.exec(cleanedSql)) !== null) {
    addTable(match[1])
  }

  // Pattern for simple unquoted: FROM tablename
  // Only if we didn't find any matches yet
  if (tables.length === 0) {
    const simplePattern = /\bFROM\s+([a-z_][a-z0-9_]*)\b/gi
    while ((match = simplePattern.exec(cleanedSql)) !== null) {
      addTable(match[1])
    }
  }

  // Match JOIN clauses
  const quotedJoinPattern = /\bJOIN\s+"[^"]+"\."[^"]+"\."([^"]+)"/gi
  while ((match = quotedJoinPattern.exec(cleanedSql)) !== null) {
    addTable(match[1])
  }

  const simpleJoinPattern = /\bJOIN\s+([a-z_][a-z0-9_]*)\b/gi
  while ((match = simpleJoinPattern.exec(cleanedSql)) !== null) {
    addTable(match[1])
  }

  console.log(
    '[extractSourceTables] Found tables:',
    tables,
    'from SQL:',
    cleanedSql.substring(0, 200),
  )

  return tables
}

/**
 * Build a WHERE clause to query upstream table based on downstream row data
 */
function buildUpstreamWhereClause(
  downstreamRow: Record<string, unknown>,
  columnMappings: Array<ColumnMapping>,
): { whereClause: string; usedColumns: Array<string> } {
  const conditions: Array<string> = []
  const usedColumns: Array<string> = []
  const mappingsByTarget = new Map(
    columnMappings.map((mapping) => [mapping.targetColumn, mapping]),
  )
  const aggregateTransformations = new Set([
    'COUNT',
    'SUM',
    'AVG',
    'MIN',
    'MAX',
    'ARRAY_AGG',
    'STRING_AGG',
  ])

  for (const [downstreamCol, value] of Object.entries(downstreamRow)) {
    // Find the mapping for this column
    const mapping = mappingsByTarget.get(downstreamCol)
    if (!mapping || !mapping.sourceColumn) continue

    // Skip aggregate functions (can't reverse them)
    if (
      mapping.transformation &&
      aggregateTransformations.has(mapping.transformation.toUpperCase())
    ) {
      continue
    }

    // Build condition based on transformation
    if (mapping.transformation) {
      const func = mapping.transformation.toUpperCase()

      if (func === 'DATE' || func === 'DATE_TRUNC') {
        // For DATE transformations, query with date range
        if (typeof value === 'string') {
          conditions.push(`DATE(${mapping.sourceColumn}) = DATE '${value}'`)
          usedColumns.push(downstreamCol)
        }
      } else if (func === 'CAST') {
        // For CAST, use original column
        if (value === null || value === undefined) {
          conditions.push(`${mapping.sourceColumn} IS NULL`)
          usedColumns.push(downstreamCol)
        } else if (typeof value === 'string') {
          conditions.push(
            `${mapping.sourceColumn} = '${value.replace(/'/g, "''")}'`,
          )
          usedColumns.push(downstreamCol)
        } else {
          conditions.push(`${mapping.sourceColumn} = ${value}`)
          usedColumns.push(downstreamCol)
        }
      } else {
        // For other functions, try to match with the function applied
        if (value === null || value === undefined) {
          conditions.push(`${mapping.sourceExpression} IS NULL`)
          usedColumns.push(downstreamCol)
        } else if (typeof value === 'string') {
          conditions.push(
            `${mapping.sourceExpression} = '${value.replace(/'/g, "''")}'`,
          )
          usedColumns.push(downstreamCol)
        } else {
          conditions.push(`${mapping.sourceExpression} = ${value}`)
          usedColumns.push(downstreamCol)
        }
      }
    } else {
      // No transformation - direct column mapping
      if (value === null || value === undefined) {
        conditions.push(`${mapping.sourceColumn} IS NULL`)
        usedColumns.push(downstreamCol)
      } else if (typeof value === 'string') {
        conditions.push(
          `${mapping.sourceColumn} = '${value.replace(/'/g, "''")}'`,
        )
        usedColumns.push(downstreamCol)
      } else if (typeof value === 'number') {
        conditions.push(`${mapping.sourceColumn} = ${value}`)
        usedColumns.push(downstreamCol)
      } else if (typeof value === 'boolean') {
        conditions.push(`${mapping.sourceColumn} = ${value}`)
        usedColumns.push(downstreamCol)
      }
    }
  }

  return { whereClause: conditions.join(' AND '), usedColumns }
}

/**
 * Detect CTEs (WITH clauses) in SQL
 */
function detectCTEs(sql: string): boolean {
  return /\bWITH\s+\w+\s+AS\s*\(/i.test(sql)
}

/**
 * Detect window functions (OVER clauses)
 */
function detectWindowFunctions(sql: string): boolean {
  return /\bOVER\s*\(/i.test(sql)
}

/**
 * Detect aggregate functions
 */
function detectAggregates(sql: string): boolean {
  return /\b(COUNT|SUM|AVG|MIN|MAX|ARRAY_AGG|STRING_AGG)\s*\(/i.test(sql)
}

/**
 * Detect GROUP BY clause
 */
function detectGroupBy(sql: string): boolean {
  return /\bGROUP\s+BY\b/i.test(sql)
}

/**
 * Detect subqueries (nested SELECT)
 */
function detectSubqueries(sql: string): boolean {
  // Look for SELECT inside parentheses (not the main SELECT)
  const withoutMainSelect = sql.replace(/^\s*SELECT/i, '')
  return /\(\s*SELECT\b/i.test(withoutMainSelect)
}

/**
 * Detect JOINs
 */
function detectJoins(sql: string): boolean {
  return /\b(INNER|LEFT|RIGHT|FULL|CROSS)?\s*JOIN\b/i.test(sql)
}

/**
 * Classify the transform type based on SQL analysis
 */
function classifyTransformType(
  hasCTEs: boolean,
  hasWindowFunctions: boolean,
  hasAggregates: boolean,
  hasGroupBy: boolean,
  hasSubqueries: boolean,
  hasJoins: boolean,
): TransformType {
  // Complex: multiple tables, subqueries, or CTEs make tracing difficult
  if (hasSubqueries || hasCTEs) {
    return 'COMPLEX'
  }

  // Many-to-one: GROUP BY or aggregates collapse rows
  if (hasGroupBy || hasAggregates) {
    return 'MANY_TO_ONE'
  }

  // One-to-many: window functions can duplicate row data across partitions
  if (hasWindowFunctions) {
    return 'ONE_TO_MANY'
  }

  // Joins add complexity but can still be 1:1 with proper keys
  if (hasJoins) {
    return 'COMPLEX'
  }

  // Default: simple 1:1 mapping
  return 'ONE_TO_ONE'
}

/**
 * Calculate confidence score for row lineage tracing
 * Returns 0-100%
 */
function calculateConfidence(
  columnMappings: Array<ColumnMapping>,
  transformType: TransformType,
  hasAggregates: boolean,
  hasGroupBy: boolean,
): { confidence: number; reasons: Array<string> } {
  let confidence = 0
  const reasons: Array<string> = []

  // +30% if parser extracted column mappings successfully
  if (columnMappings.length > 0) {
    confidence += 30
    reasons.push('Column mappings extracted successfully')
  } else {
    reasons.push('Could not extract column mappings')
  }

  // +30% if no aggregates
  if (!hasAggregates) {
    confidence += 30
    reasons.push('No aggregate functions detected')
  } else {
    reasons.push('Aggregates detected - row tracing limited')
  }

  // +20% if no GROUP BY
  if (!hasGroupBy) {
    confidence += 20
    reasons.push('No GROUP BY clause')
  } else {
    reasons.push('GROUP BY detected - multiple rows collapsed')
  }

  // +20% if we have traceable columns (columns with sourceColumn)
  const traceableColumns = columnMappings.filter((m) => m.sourceColumn)
  if (traceableColumns.length > 0) {
    confidence += 20
    reasons.push(`${traceableColumns.length} traceable columns found`)
  } else {
    reasons.push('No traceable columns identified')
  }

  // Reduce confidence for complex transforms
  if (transformType === 'COMPLEX') {
    confidence = Math.max(0, confidence - 20)
    reasons.push('Complex SQL structure reduces confidence')
  }

  return { confidence, reasons }
}

/**
 * Analyze SQL to extract full transformation information
 */
export function analyzeSQLTransformation(sql: string): SQLAnalysis {
  // Detect SQL patterns
  const hasCTEs = detectCTEs(sql)
  const hasWindowFunctions = detectWindowFunctions(sql)
  const hasAggregates = detectAggregates(sql)
  const hasGroupBy = detectGroupBy(sql)
  const hasSubqueries = detectSubqueries(sql)
  const hasJoins = detectJoins(sql)

  // Parse column mappings
  const columnMappings = parseColumnMappings(sql)
  const sourceTables = extractSourceTables(sql)

  // Classify transform type
  const transformType = classifyTransformType(
    hasCTEs,
    hasWindowFunctions,
    hasAggregates,
    hasGroupBy,
    hasSubqueries,
    hasJoins,
  )

  // Calculate confidence
  const { confidence, reasons } = calculateConfidence(
    columnMappings,
    transformType,
    hasAggregates,
    hasGroupBy,
  )

  return {
    columnMappings,
    sourceTables,
    transformType,
    confidence,
    confidenceReasons: reasons,
    hasCTEs,
    hasWindowFunctions,
    hasAggregates,
    hasGroupBy,
    hasSubqueries,
    hasJoins,
  }
}

/**
 * Column priority type for smart matching
 */
type ColumnPriority =
  | 'primary_key'
  | 'id'
  | 'timestamp'
  | 'categorical'
  | 'numeric'
  | 'other'

/**
 * Key column info for smarter row matching
 */
interface KeyColumnInfo {
  name: string
  priority: ColumnPriority
  isId: boolean
  isTimestamp: boolean
  isBatchId: boolean
}

const BATCH_ID_PATTERN = /batch_id/
const ID_PATTERN = /uuid|_key/
const TIMESTAMP_PATTERN = /created_at|updated_at|timestamp|_at|_date/

/**
 * Detect key columns in a row based on common patterns
 * Looks for: id, _id, uuid, created_at, updated_at, _dlt_load_id, etc.
 */
function detectKeyColumns(columnNames: Array<string>): Array<KeyColumnInfo> {
  const keyColumns: Array<KeyColumnInfo> = []

  for (const name of columnNames) {
    const lower = name.toLowerCase()

    // DLT batch identifiers (highest priority for batch lineage)
    if (
      lower === '_dlt_load_id' ||
      lower === '_dlt_id' ||
      BATCH_ID_PATTERN.test(lower)
    ) {
      keyColumns.push({
        name,
        priority: 'primary_key',
        isId: true,
        isTimestamp: false,
        isBatchId: true,
      })
      continue
    }

    // Primary key patterns
    if (lower === 'id' || lower === 'pk' || lower.endsWith('_pk')) {
      keyColumns.push({
        name,
        priority: 'primary_key',
        isId: true,
        isTimestamp: false,
        isBatchId: false,
      })
      continue
    }

    // Other ID patterns (foreign keys, UUIDs)
    if (lower.endsWith('_id') || ID_PATTERN.test(lower)) {
      keyColumns.push({
        name,
        priority: 'id',
        isId: true,
        isTimestamp: false,
        isBatchId: false,
      })
      continue
    }

    // Timestamp patterns
    if (TIMESTAMP_PATTERN.test(lower) || lower === 'date') {
      keyColumns.push({
        name,
        priority: 'timestamp',
        isId: false,
        isTimestamp: true,
        isBatchId: false,
      })
      continue
    }
  }

  return keyColumns
}

/**
 * Get column priority score for sorting
 * Higher score = better for matching
 */
function getColumnPriority(priority: ColumnPriority): number {
  const scores: Record<ColumnPriority, number> = {
    primary_key: 100,
    id: 80,
    timestamp: 60,
    categorical: 40,
    numeric: 20,
    other: 0,
  }
  return scores[priority]
}

/**
 * Build a smart WHERE clause using key columns preferentially
 *
 * Priority order:
 * 1. Primary keys / batch IDs (e.g., _dlt_load_id, id)
 * 2. Regular IDs (e.g., user_id, event_id)
 * 3. Timestamps (e.g., created_at)
 * 4. Fall back to all available mappings
 */
export function buildSmartWhereClause(
  rowData: Record<string, unknown>,
  columnMappings: Array<ColumnMapping>,
  maxConditions: number = 3,
): { whereClause: string; usedColumns: Array<string>; strategy: string } {
  const columnNames = Object.keys(rowData)
  const keyColumns = detectKeyColumns(columnNames)
  const conditions: Array<string> = []
  const usedColumns: Array<string> = []

  // Sort key columns by priority
  const sortedKeyColumns = keyColumns
    .slice()
    .sort(
      (a, b) => getColumnPriority(b.priority) - getColumnPriority(a.priority),
    )
  const mappingsByTarget = new Map(
    columnMappings.map((mapping) => [
      mapping.targetColumn.toLowerCase(),
      mapping,
    ]),
  )

  // First, try to use key columns that exist in both rowData and mappings
  for (const keyCol of sortedKeyColumns) {
    if (conditions.length >= maxConditions) break

    const value = rowData[keyCol.name]
    if (value === undefined) continue

    // Check if this column has a mapping to source
    const mapping = mappingsByTarget.get(keyCol.name.toLowerCase())

    console.log(
      `[buildSmartWhereClause] keyCol: ${keyCol.name}, mapping found:`,
      mapping
        ? { target: mapping.targetColumn, source: mapping.sourceColumn }
        : 'none',
    )

    // Without an explicit mapping, assume the key column keeps its name upstream.
    const sourceCol = mapping?.sourceColumn || keyCol.name

    console.log(`[buildSmartWhereClause] Using sourceCol: ${sourceCol}`)

    // Build condition
    const condition = buildCondition(sourceCol, value)
    if (condition) {
      conditions.push(condition)
      usedColumns.push(keyCol.name)
    }
  }

  // If we have key conditions, use them
  if (conditions.length > 0) {
    return {
      whereClause: conditions.join(' AND '),
      usedColumns,
      strategy:
        conditions.length === 1 && sortedKeyColumns[0]?.isBatchId
          ? 'batch_id'
          : 'key_columns',
    }
  }

  // Fall back to regular column mappings
  const fallbackResult = buildUpstreamWhereClause(rowData, columnMappings)
  return {
    whereClause: fallbackResult.whereClause,
    usedColumns: fallbackResult.usedColumns,
    strategy: 'column_mappings',
  }
}

/**
 * Build a single SQL condition for a column/value pair
 */
function buildCondition(column: string, value: unknown): string | null {
  if (value === null || value === undefined) {
    return `${column} IS NULL`
  }

  if (typeof value === 'string') {
    const escaped = value.replace(/'/g, "''")

    // Detect timestamp-like values and use TIMESTAMP cast for Trino
    // Matches: 2025-12-02, 2025-12-02 00:00:00, 2025-12-02T00:00:00, etc.
    if (/^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2})?/.test(value)) {
      return `${column} = TIMESTAMP '${escaped}'`
    }

    return `${column} = '${escaped}'`
  }

  if (typeof value === 'number') {
    return `${column} = ${value}`
  }

  if (typeof value === 'boolean') {
    return `${column} = ${value}`
  }

  // Unlike the string branch above, Date values are emitted as plain ISO
  // literals without the TIMESTAMP cast.
  if (value instanceof Date) {
    return `${column} = '${value.toISOString()}'`
  }

  return null
}
