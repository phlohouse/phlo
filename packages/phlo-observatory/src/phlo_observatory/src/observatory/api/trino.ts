/**
 * Trino Server Functions
 *
 * Thin wrappers that forward to phlo-api (Python backend).
 * Preserves SSR while keeping business logic in Python.
 */

import { createServerFn } from '@tanstack/react-start'

import { authMiddleware } from '@/observatory/api/auth'
import { apiGet, apiPost } from '@/server/phlo-api'

// Types for Trino responses
export interface DataRow {
  [key: string]: string | number | boolean | null | undefined
}

export interface DataPreviewResult {
  columns: Array<string>
  columnTypes: Array<string>
  rows: Array<DataRow>
  totalRows?: number
  hasMore: boolean
}

export interface QueryExecutionError {
  ok: false
  error: string
  kind: 'timeout' | 'trino' | 'validation'
}

export type QueryExecutionResult = DataPreviewResult & {
  effectiveQuery: string
}

// Python API response types (snake_case)
interface ApiDataPreviewResult {
  columns: Array<string>
  column_types: Array<string>
  rows: Array<DataRow>
  total_rows?: number
  row_count?: number
  has_more: boolean
}

interface ApiQueryResult extends ApiDataPreviewResult {
  effective_query?: string
  effective_sql?: string
}

interface ApiRowJourney {
  row: DataRow
}

// Transform functions
function transformPreviewResult(r: ApiDataPreviewResult): DataPreviewResult {
  return {
    columns: r.columns,
    columnTypes: r.column_types,
    rows: r.rows,
    totalRows: r.total_rows ?? r.row_count,
    hasMore: r.has_more,
  }
}

export async function previewDataFromApi(data: {
  table: string
  limit?: number
  offset?: number
}): Promise<DataPreviewResult | { error: string }> {
  const result = await apiGet<ApiDataPreviewResult | { error: string }>(
    `/api/observatory/table-preview/${encodeURIComponent(data.table)}`,
    { limit: data.limit ?? 100, offset: data.offset ?? 0 },
  )

  if ('error' in result) return result
  return transformPreviewResult(result)
}

export async function executeQueryFromApi(data: {
  query: string
  branch?: string
  defaultLimit?: number
  offset?: number
}): Promise<QueryExecutionResult | QueryExecutionError> {
  const result = await apiPost<
    ApiQueryResult | QueryExecutionError | { error: string }
  >('/api/observatory/query', {
    sql: data.query,
    branch: data.branch ?? 'main',
    limit: data.defaultLimit ?? 100,
    offset: data.offset ?? 0,
  })

  // The backend answers with either a structured QueryExecutionError
  // ({ok: false, kind: ...}) or a plain {error} string; normalize both into
  // QueryExecutionError so callers handle a single failure shape.
  if ('ok' in result && result.ok === false) return result
  if ('error' in result) {
    return { ok: false, error: result.error, kind: 'trino' }
  }

  return {
    ...transformPreviewResult(result),
    effectiveQuery:
      result.effective_sql ?? result.effective_query ?? data.query,
  }
}

export async function getRowByIdFromApi(data: {
  table: string
  rowId: string
}): Promise<DataPreviewResult | { error: string }> {
  const result = await apiGet<ApiRowJourney | { error: string }>(
    `/api/observatory/row-journey/${encodeURIComponent(data.table)}/${encodeURIComponent(data.rowId)}`,
  )

  if ('error' in result) return result
  const columns = Object.keys(result.row)
  return {
    columns,
    columnTypes: columns.map(() => 'unknown'),
    rows: [result.row],
    totalRows: 1,
    hasMore: false,
  }
}

/**
 * Preview data from a table with pagination
 */
export const previewData = createServerFn()
  .middleware([authMiddleware])
  .inputValidator(
    (input: {
      table: string
      branch?: string
      catalog?: string
      schema?: string
      limit?: number
      offset?: number
      trinoUrl?: string
      timeoutMs?: number
      maxLimit?: number
    }) => input,
  )
  .handler(
    async ({
      data: { table, limit = 100, offset = 0 },
    }): Promise<DataPreviewResult | { error: string }> => {
      try {
        return await previewDataFromApi({ table, limit, offset })
      } catch (error) {
        return {
          error: error instanceof Error ? error.message : 'Unknown error',
        }
      }
    },
  )

/**
 * Run an arbitrary query through phlo-api.
 *
 * Guardrail inputs (readOnlyMode, limits, timeouts) are validated but not
 * forwarded: enforcement happens entirely in the Python backend, which
 * returns the effective (rewritten) SQL alongside the results.
 */
export const executeQuery = createServerFn()
  .middleware([authMiddleware])
  .inputValidator(
    (input: {
      query: string
      branch?: string
      catalog?: string
      schema?: string
      trinoUrl?: string
      timeoutMs?: number
      readOnlyMode?: boolean
      defaultLimit?: number
      maxLimit?: number
      allowUnsafe?: boolean
    }) => input,
  )
  .handler(
    async ({
      data: { query, branch = 'main', defaultLimit = 100 },
    }): Promise<QueryExecutionResult | QueryExecutionError> => {
      try {
        return await executeQueryFromApi({ query, branch, defaultLimit })
      } catch (error) {
        return {
          ok: false,
          error: error instanceof Error ? error.message : 'Unknown error',
          kind: 'trino',
        }
      }
    },
  )

/**
 * Get a single row by its _phlo_row_id
 */
export const getRowById = createServerFn()
  .middleware([authMiddleware])
  .inputValidator(
    (input: {
      table: string
      rowId: string
      catalog?: string
      schema?: string
      trinoUrl?: string
      timeoutMs?: number
    }) => input,
  )
  .handler(
    async ({
      data: { table, rowId },
    }): Promise<DataPreviewResult | { error: string }> => {
      try {
        return await getRowByIdFromApi({ table, rowId })
      } catch (error) {
        return {
          error: error instanceof Error ? error.message : 'Unknown error',
        }
      }
    },
  )
