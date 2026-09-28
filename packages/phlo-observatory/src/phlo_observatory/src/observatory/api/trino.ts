/** Query-workspace client for the environment-scoped v1 API. */

import { createMiddleware, createServerFn } from '@tanstack/react-start'
import { z } from 'zod'

import type { ObservatoryEnvironment } from '@/observatory/api/environment'
import { selectedEnvironment, v1Endpoint } from '@/observatory/api/environment'
import { authMiddleware } from '@/observatory/api/auth'
import { apiGet, apiPost } from '@/server/phlo-api'
import {
  mutationAuthorization,
  mutationBearerAuthorization,
} from '@/server/authenticated-mutation'

const V1_QUERY_PREFIX = '/api/v1'

type JsonValue =
  | string
  | number
  | boolean
  | null
  | Array<JsonValue>
  | { [key: string]: JsonValue }

export type DataRow = Record<string, JsonValue>

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

export type QueryStatus =
  'queued' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled'

export interface QuerySessionView {
  id: string
  env: ObservatoryEnvironment
  nessie_ref: string
  status: QueryStatus
  sql_hash: string
  created_at: string
  updated_at: string
  result: Record<string, unknown> | null
  error: string | null
}

export interface SavedQueryView {
  id: string
  env: ObservatoryEnvironment
  nessie_ref: string
  name: string
  sql: string
  version: number
  created_at: string
  updated_at: string
  metadata: Record<string, JsonValue>
}

export interface QueryRefView {
  env: ObservatoryEnvironment
  name: string
  catalog: string
}

export interface QueryCatalogView {
  env: ObservatoryEnvironment
  nessie_ref: string
  engine: string
  catalogs: Array<{
    name: string
    truncated: boolean
    schemas: Array<{ name: string; tables: Array<string> }>
  }>
}

const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() =>
  z.union([
    z.string(),
    z.number(),
    z.boolean(),
    z.null(),
    z.array(jsonValueSchema),
    z.record(z.string(), jsonValueSchema),
  ]),
)

const queryResultSchema = z.object({
  columns: z.array(z.object({ name: z.string(), type: z.string() })),
  rows: z.array(z.record(z.string(), jsonValueSchema)),
  has_more: z.boolean(),
})

const QUERY_POLL_INTERVAL_MS = 250
const QUERY_WAIT_LIMIT_MS = 30_000

interface QueryRequest {
  sql: string
  row_limit: number
}

function environmentForRequest(
  environment: ObservatoryEnvironment | null | undefined,
): ObservatoryEnvironment {
  const selected = environment ?? selectedEnvironment()
  if (selected === 'prod' || selected === 'staging') return selected
  throw new Error(
    'Select prod or staging before loading environment-scoped data.',
  )
}

function queryEndpoint(
  path: string,
  environment?: ObservatoryEnvironment,
): string {
  return v1Endpoint(path, environmentForRequest(environment))
}

/** Submit a bounded, read-only SQL query. The API always returns a session. */
export async function submitQueryFromApi(data: {
  query: string
  rowLimit?: number
  environment?: ObservatoryEnvironment
  authorization?: string
}): Promise<QuerySessionView> {
  const payload: QueryRequest = {
    sql: data.query,
    row_limit: data.rowLimit ?? 100,
  }
  return apiPost<QuerySessionView>(
    queryEndpoint(`${V1_QUERY_PREFIX}/queries`, data.environment),
    payload,
    30000,
    data.authorization,
  )
}

export async function getQuerySessionFromApi(data: {
  queryId: string
  environment?: ObservatoryEnvironment
  authorization?: string
}): Promise<QuerySessionView> {
  return apiGet<QuerySessionView>(
    queryEndpoint(
      `${V1_QUERY_PREFIX}/queries/${encodeURIComponent(data.queryId)}`,
      data.environment,
    ),
    undefined,
    30000,
    data.authorization,
  )
}

export async function explainQueryFromApi(data: {
  query: string
  rowLimit?: number
  environment?: ObservatoryEnvironment
  authorization?: string
}): Promise<QuerySessionView> {
  return apiPost<QuerySessionView>(
    queryEndpoint(`${V1_QUERY_PREFIX}/queries/explain`, data.environment),
    { sql: data.query, row_limit: data.rowLimit ?? 100 } satisfies QueryRequest,
    30000,
    data.authorization,
  )
}

export async function cancelQueryFromApi(data: {
  queryId: string
  environment?: ObservatoryEnvironment
  authorization?: string
}): Promise<QuerySessionView> {
  return apiPost<QuerySessionView>(
    queryEndpoint(
      `${V1_QUERY_PREFIX}/queries/${encodeURIComponent(data.queryId)}/cancel`,
      data.environment,
    ),
    undefined,
    30000,
    data.authorization,
  )
}

export async function executeQueryFromApi(data: {
  query: string
  defaultLimit?: number
  environment?: ObservatoryEnvironment
  authorization?: string
}): Promise<QueryExecutionResult | QueryExecutionError> {
  const authorization = data.authorization
  let session = await submitQueryFromApi({
    query: data.query,
    rowLimit: data.defaultLimit,
    environment: data.environment,
    authorization,
  })
  const deadline = Date.now() + QUERY_WAIT_LIMIT_MS
  while (
    session.status === 'queued' ||
    session.status === 'running' ||
    session.status === 'cancelling'
  ) {
    if (Date.now() >= deadline) {
      await cancelQueryFromApi({
        queryId: session.id,
        environment: session.env,
        authorization,
      })
      return {
        ok: false,
        kind: 'timeout',
        error: 'Query exceeded the workspace wait limit.',
      }
    }
    await new Promise((resolve) => setTimeout(resolve, QUERY_POLL_INTERVAL_MS))
    session = await getQuerySessionFromApi({
      queryId: session.id,
      environment: session.env,
      authorization,
    })
  }
  if (session.status !== 'completed' || !session.result) {
    return {
      ok: false,
      kind: 'trino',
      error: session.error ?? `Query ${session.status}.`,
    }
  }
  const result = queryResultSchema.safeParse(session.result)
  if (!result.success) {
    return {
      ok: false,
      kind: 'trino',
      error: 'Query returned an invalid result.',
    }
  }
  return {
    columns: result.data.columns.map((column) => column.name),
    columnTypes: result.data.columns.map((column) => column.type),
    rows: result.data.rows,
    hasMore: result.data.has_more,
    effectiveQuery: data.query,
  }
}

const queryAuthorization = createMiddleware({ type: 'request' }).server(
  ({ next, request }) =>
    next({
      context: {
        authorization: mutationBearerAuthorization(
          request.headers.get('authorization'),
        ),
      },
    }),
)

const savedQueryInputSchema = z.object({
  environment: z.enum(['prod', 'staging']),
  name: z.string().trim().min(1).max(120),
  sql: z
    .string()
    .trim()
    .min(1)
    .max(64 * 1024),
  metadata: z.record(z.string(), jsonValueSchema).default({}),
})
const savedQueryViewSchema = z.object({
  id: z.string(),
  env: z.enum(['prod', 'staging']),
  nessie_ref: z.string(),
  name: z.string(),
  sql: z.string(),
  version: z.number().int().positive(),
  created_at: z.string(),
  updated_at: z.string(),
  metadata: z.record(z.string(), jsonValueSchema),
})
const queryCatalogSchema = z.object({
  env: z.enum(['prod', 'staging']),
  nessie_ref: z.string(),
  engine: z.string(),
  catalogs: z.array(
    z.object({
      name: z.string(),
      truncated: z.boolean(),
      schemas: z.array(
        z.object({ name: z.string(), tables: z.array(z.string()) }),
      ),
    }),
  ),
})

export async function getQueryCatalogFromApi(data: {
  environment: ObservatoryEnvironment
  authorization?: string
}): Promise<QueryCatalogView> {
  const response = await apiGet<unknown>(
    queryEndpoint(`${V1_QUERY_PREFIX}/query/catalog`, data.environment),
    undefined,
    30000,
    data.authorization,
  )
  return queryCatalogSchema.parse(response)
}

export const getQueryCatalog = createServerFn()
  .middleware([queryAuthorization])
  .inputValidator(z.object({ environment: z.enum(['prod', 'staging']) }))
  .handler(({ data, context }) =>
    getQueryCatalogFromApi({
      environment: data.environment,
      authorization: context.authorization,
    }),
  )

export async function getSavedQueriesFromApi(data: {
  environment: ObservatoryEnvironment
  authorization?: string
}): Promise<Array<SavedQueryView>> {
  const response = await apiGet<unknown>(
    queryEndpoint(`${V1_QUERY_PREFIX}/queries/saved`, data.environment),
    undefined,
    30000,
    data.authorization,
  )
  return z.object({ items: z.array(savedQueryViewSchema) }).parse(response)
    .items
}

export async function createSavedQueryFromApi(data: {
  environment: ObservatoryEnvironment
  idempotencyKey: string
  name: string
  sql: string
  metadata?: Record<string, JsonValue>
  authorization?: string
}): Promise<SavedQueryView> {
  const response = await apiPost<unknown>(
    queryEndpoint(`${V1_QUERY_PREFIX}/queries/saved`, data.environment),
    {
      env: data.environment,
      name: data.name,
      sql: data.sql,
      metadata: data.metadata ?? {},
    },
    30000,
    data.authorization,
    { 'Idempotency-Key': data.idempotencyKey },
  )
  return savedQueryViewSchema.parse(response)
}

export const getQueryRefs = createServerFn()
  .middleware([queryAuthorization])
  .inputValidator(z.object({ environment: z.enum(['prod', 'staging']) }))
  .handler(async ({ data, context }): Promise<Array<QueryRefView>> => {
    const response = await apiGet<{ items: Array<QueryRefView> }>(
      queryEndpoint(`${V1_QUERY_PREFIX}/query/refs`, data.environment),
      undefined,
      30000,
      context.authorization,
    )
    return z
      .array(
        z.object({
          env: z.enum(['prod', 'staging']),
          name: z.string(),
          catalog: z.string(),
        }),
      )
      .parse(response.items)
  })

export const getSavedQueries = createServerFn()
  .middleware([queryAuthorization])
  .inputValidator(z.object({ environment: z.enum(['prod', 'staging']) }))
  .handler(({ data, context }) =>
    getSavedQueriesFromApi({
      environment: data.environment,
      authorization: context.authorization,
    }),
  )

export const createSavedQuery = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator(
    savedQueryInputSchema.extend({
      environment: z.enum(['prod', 'staging']),
      idempotencyKey: z.string().trim().min(1).max(200),
    }),
  )
  .handler(({ data, context }) =>
    createSavedQueryFromApi({
      ...data,
      authorization: context.authorization,
    }),
  )

export const executeQuery = createServerFn()
  .middleware([queryAuthorization, authMiddleware])
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
      environment?: ObservatoryEnvironment
      authToken?: string
    }) => input,
  )
  .handler(
    async ({
      data,
      context,
    }): Promise<QueryExecutionResult | QueryExecutionError> => {
      try {
        return await executeQueryFromApi({
          query: data.query,
          defaultLimit: data.defaultLimit,
          environment: data.environment,
          authorization: context.authorization,
        })
      } catch (error) {
        return {
          ok: false,
          error: error instanceof Error ? error.message : 'Unknown error',
          kind: 'trino',
        }
      }
    },
  )
