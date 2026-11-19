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
