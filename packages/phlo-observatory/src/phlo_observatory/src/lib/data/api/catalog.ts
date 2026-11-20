/** Browses the lakehouse catalog one level at a time: schemas, tables and views, columns. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { PhloApiError, environmentSchema, phloApi } from './client'

const objectsSchema = z.object({
  env: environmentSchema,
  nessie_ref: z.string(),
  catalog: z.string(),
  schema_name: z.string().nullable(),
  table: z.string().nullable(),
  items: z.array(
    z.object({
      name: z.string(),
      kind: z.enum(['schema', 'table', 'view', 'column']),
      data_type: z.string().nullable(),
      nullable: z.boolean().nullable(),
    }),
  ),
  next_offset: z.number().int().nullable(),
})

export type CatalogObjects = z.infer<typeof objectsSchema>
export type CatalogObject = CatalogObjects['items'][number]

const objectName = z.string().min(1).max(256)

export const getCatalogObjects = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      schema: objectName.optional(),
      table: objectName.optional(),
      offset: z.number().int().min(0).max(100_000).default(0),
    }),
  )
  .handler(async ({ data }) => {
    const params = new URLSearchParams({ env: data.env })
    if (data.schema) params.set('schema', data.schema)
    if (data.table) params.set('table', data.table)
    if (data.offset) params.set('offset', String(data.offset))
    try {
      return {
        state: 'available' as const,
        page: await phloApi(
          `/api/v1/query/catalog/objects?${params.toString()}`,
          objectsSchema,
          { env: data.env },
        ),
      }
    } catch (error) {
      if (!(error instanceof PhloApiError) || error.status !== 503) throw error
      return {
        state: 'unavailable' as const,
        message:
          'The lakehouse catalog is not reachable. Query preview has to be set up for this environment.',
      }
    }
  })

const detailsSchema = z.object({
  env: environmentSchema,
  nessie_ref: z.string(),
  catalog: z.string(),
  schema_name: z.string(),
  table: z.string(),
  kind: z.enum(['table', 'view']),
  comment: z.string().nullable(),
  columns: z.array(
    z.object({
      name: z.string(),
      data_type: z.string().nullable(),
      comment: z.string().nullable(),
    }),
  ),
  ddl: z.string().nullable(),
  row_count: z.number().int().nullable(),
  snapshot_id: z.string().nullable(),
  committed_at: z.string().nullable(),
})
const sampleSchema = z.object({
  env: environmentSchema,
  schema_name: z.string(),
  table: z.string(),
  columns: z.array(
    z.object({ name: z.string(), type: z.string().nullable().optional() }),
  ),
  rows: z.array(z.record(z.string(), z.json())),
  has_more: z.boolean(),
})
const searchSchema = z.object({
  env: environmentSchema,
  catalog: z.string(),
  q: z.string(),
  items: z.array(
    z.object({
      schema_name: z.string(),
      table: z.string(),
      kind: z.enum(['table', 'view', 'column']),
      column: z.string().nullable(),
      data_type: z.string().nullable(),
    }),
  ),
  truncated: z.boolean(),
})

export type CatalogDetails = z.infer<typeof detailsSchema>
export type CatalogSample = z.infer<typeof sampleSchema>
export type CatalogHit = z.infer<typeof searchSchema>['items'][number]

const tableInput = z.object({
  env: environmentSchema,
  schema: objectName,
  table: objectName,
})

/** Reports a missing table or an unreachable engine as data the panel can show. */
async function catalogRead<T>(request: () => Promise<T>) {
  try {
    return { state: 'ok' as const, data: await request() }
  } catch (error) {
    if (
      !(error instanceof PhloApiError) ||
      ![403, 404, 503].includes(error.status)
    )
      throw error
    return {
      state: 'unavailable' as const,
      message:
        error.status === 404
          ? 'That table is no longer in the catalog.'
          : error.status === 403
            ? 'You can browse the catalog here but not read rows.'
            : (error.detail ?? 'The lakehouse catalog is not reachable.'),
    }
  }
}

/** Columns with comments, the definition (DDL) and, for Iceberg tables, the row count. */
export const getCatalogDetails = createServerFn({ method: 'GET' })
  .inputValidator(tableInput)
  .handler(({ data }) =>
    catalogRead(() =>
      phloApi(
        `/api/v1/query/catalog/details?env=${data.env}&schema=${encodeURIComponent(data.schema)}&table=${encodeURIComponent(data.table)}`,
        detailsSchema,
        { env: data.env, timeoutMs: 45_000 },
      ),
    ),
  )

/** The first 50 rows, through the same read-only preview path as the query page. */
export const getCatalogSample = createServerFn({ method: 'GET' })
  .inputValidator(tableInput)
  .handler(({ data }) =>
    catalogRead(() =>
      phloApi(
        `/api/v1/query/catalog/sample?env=${data.env}&schema=${encodeURIComponent(data.schema)}&table=${encodeURIComponent(data.table)}`,
        sampleSchema,
        { env: data.env, timeoutMs: 45_000 },
      ),
    ),
  )

/** Schemas, tables, views and columns whose names contain `q`, across the whole catalog. */
export const searchCatalog = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({ env: environmentSchema, q: z.string().min(2).max(64) }),
  )
  .handler(({ data }) =>
    catalogRead(() =>
      phloApi(
        `/api/v1/query/catalog/search?env=${data.env}&q=${encodeURIComponent(data.q)}`,
        searchSchema,
        { env: data.env, timeoutMs: 30_000 },
      ),
    ),
  )
