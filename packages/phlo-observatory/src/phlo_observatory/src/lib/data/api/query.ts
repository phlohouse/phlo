/** Validates and executes query sessions, explanations, and saved-query operations. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

const columnSchema = z.object({ name: z.string(), type: z.string().nullable() })
const resultSchema = z.object({
  columns: z.array(columnSchema),
  rows: z.array(z.record(z.string(), z.json())),
  has_more: z.boolean(),
})
const sessionSchema = z.object({
  id: z.string(),
  env: environmentSchema,
  nessie_ref: z.string(),
  status: z.enum([
    'queued',
    'running',
    'cancelling',
    'completed',
    'failed',
    'cancelled',
  ]),
  sql_hash: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  result: resultSchema.nullable(),
  error: z.string().nullable(),
})
const savedQuerySchema = z.object({
  id: z.string(),
  env: environmentSchema,
  nessie_ref: z.string(),
  name: z.string(),
  sql: z.string(),
  version: z.number().int().positive(),
  created_at: z.string(),
  updated_at: z.string(),
  metadata: z.record(z.string(), z.json()),
})
const inputSchema = z.object({ env: environmentSchema })
const queryInputSchema = inputSchema.extend({
  sql: z
    .string()
    .min(1)
    .max(64 * 1024),
})
const sessionInputSchema = inputSchema.extend({ id: z.string().min(1) })
const saveInputSchema = queryInputSchema.extend({
  id: z.string().optional(),
  name: z.string().min(1).max(120),
  version: z.number().int().positive().optional(),
  idempotencyKey: z.string().min(1),
})

export type QuerySession = z.infer<typeof sessionSchema>
export type QueryResult = z.infer<typeof resultSchema>
export type SavedQuery = z.infer<typeof savedQuerySchema>

export const getQueryWorkspace = createServerFn({ method: 'GET' })
  .validator(inputSchema)
  .handler(async ({ data }) => {
    const env = data.env
    const [catalog, refs, engines, saved] = await Promise.all([
      phloApi(
        `/api/v1/query/catalog?env=${env}`,
        z.object({
          env: environmentSchema,
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
        }),
        { env },
      ),
      phloApi(
        `/api/v1/query/refs?env=${env}`,
        z.object({
          env: environmentSchema,
          items: z.array(
            z.object({
              env: environmentSchema,
              name: z.string(),
              catalog: z.string(),
            }),
          ),
        }),
        { env },
      ),
      phloApi(
        `/api/v1/query/engines?env=${env}`,
        z.object({
          env: environmentSchema,
          items: z.array(
            z.object({
              id: z.string(),
              status: z.enum(['configured', 'unavailable']),
            }),
          ),
        }),
        { env },
      ),
      phloApi(
        `/api/v1/queries/saved?env=${env}`,
        z.object({ env: environmentSchema, items: z.array(savedQuerySchema) }),
        { env },
      ),
    ])
    return {
      catalog,
      refs: refs.items,
      engines: engines.items,
      saved: saved.items,
    }
  })

export const submitQuery = createServerFn({ method: 'POST' })
  .validator(queryInputSchema)
  .handler(({ data }) =>
    phloApi(`/api/v1/queries?env=${data.env}`, sessionSchema, {
      env: data.env,
      method: 'POST',
      body: { sql: data.sql, row_limit: 100 },
    }),
  )
export const explainQuery = createServerFn({ method: 'POST' })
  .validator(queryInputSchema)
  .handler(({ data }) =>
    phloApi(`/api/v1/queries/explain?env=${data.env}`, sessionSchema, {
      env: data.env,
      method: 'POST',
      body: { sql: data.sql, row_limit: 100 },
    }),
  )

export const getQuerySession = createServerFn({ method: 'GET' })
  .validator(sessionInputSchema)
  .handler(({ data }) =>
    phloApi(
      `/api/v1/queries/${encodeURIComponent(data.id)}?env=${data.env}`,
      sessionSchema,
      { env: data.env },
    ),
  )
export const cancelQuery = createServerFn({ method: 'POST' })
  .validator(sessionInputSchema)
  .handler(({ data }) =>
    phloApi(
      `/api/v1/queries/${encodeURIComponent(data.id)}/cancel?env=${data.env}`,
      sessionSchema,
      { env: data.env, method: 'POST' },
    ),
  )
export const downloadQueryCsv = createServerFn({ method: 'GET' })
  .validator(sessionInputSchema)
  .handler(({ data }) =>
    phloApi(
      `/api/v1/queries/${encodeURIComponent(data.id)}/csv?env=${data.env}`,
      z.string(),
      { env: data.env, responseType: 'text' },
    ),
  )

export const saveQuery = createServerFn({ method: 'POST' })
  .validator(saveInputSchema)
  .handler(({ data }) => {
    const body = {
      env: data.env,
      name: data.name,
      sql: data.sql,
      metadata: {},
      ...(data.version ? { expected_version: data.version } : {}),
    }
    return phloApi(
      `${data.id ? `/api/v1/queries/saved/${encodeURIComponent(data.id)}` : '/api/v1/queries/saved'}?env=${data.env}`,
      savedQuerySchema,
      {
        env: data.env,
        method: data.id ? 'PUT' : 'POST',
        body,
        idempotencyKey: data.idempotencyKey,
      },
    )
  })

export const deleteSavedQuery = createServerFn({ method: 'POST' })
  .validator(
    saveInputSchema
      .pick({ env: true, id: true, version: true, idempotencyKey: true })
      .required({ id: true, version: true }),
  )
  .handler(async ({ data }) => {
    await phloApi(
      `/api/v1/queries/saved/${encodeURIComponent(data.id)}?env=${data.env}`,
      z.string(),
      {
        env: data.env,
        method: 'DELETE',
        body: { env: data.env, expected_version: data.version },
        idempotencyKey: data.idempotencyKey,
        responseType: 'text',
      },
    )
    return { id: data.id }
  })
