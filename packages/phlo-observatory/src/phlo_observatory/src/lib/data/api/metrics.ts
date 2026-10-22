/** Draft metric model persistence; execution uses the existing scoped Query API. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

const identifier = z.string().regex(/^[A-Za-z_][A-Za-z0-9_]{0,127}$/)
const dimensionSchema = z.object({
  name: identifier,
  column: identifier,
  kind: z.enum(['string', 'number', 'boolean', 'time']),
  description: z.string(),
})
const measureSchema = z.object({
  name: identifier,
  column: identifier,
  aggregation: z.enum(['count', 'count_distinct', 'sum', 'avg', 'min', 'max']),
  description: z.string(),
})
export const metricDefinitionSchema = z.object({
  model_id: z.string().regex(/^[a-z][a-z0-9_-]{0,62}$/),
  description: z.string().min(1).max(4000),
  owner: z.string().min(1).max(160),
  asset_id: z.string().min(1),
  dimensions: z.array(dimensionSchema).max(20),
  measures: z.array(measureSchema).min(1).max(20),
  time_dimension: identifier.nullable(),
})
const modelSchema = metricDefinitionSchema.extend({
  env: environmentSchema,
  nessie_ref: z.string(),
  relation: z.string(),
  version: z.number().int().positive(),
  status: z.literal('draft'),
  created_at: z.string(),
  updated_at: z.string(),
})
const inventorySchema = z.object({
  env: environmentSchema,
  items: z.array(modelSchema),
  publication: z.literal('draft_definitions_only'),
})
const sourceSchema = z.object({
  id: z.string(),
  relation: z.string().nullable(),
})
const sourceDetailSchema = sourceSchema.extend({
  columns: z.array(
    z.object({
      name: z.string(),
      type: z.string().nullable(),
      description: z.string().nullable(),
    }),
  ),
})
export type MetricDefinition = z.infer<typeof metricDefinitionSchema>
export type MetricModel = z.infer<typeof modelSchema>
export type MetricSource = z.infer<typeof sourceDetailSchema>

export const getMetricsWorkspace = createServerFn({ method: 'GET' })
  .inputValidator(z.object({ env: environmentSchema }))
  .handler(async ({ data: { env } }) => {
    const [models, sources] = await Promise.all([
      phloApi(`api/v1/metric-models?env=${env}`, inventorySchema, { env }),
      phloApi(
        `api/v1/assets?env=${env}&limit=100`,
        z.object({
          env: environmentSchema,
          items: z.array(sourceSchema),
          next_cursor: z.string().nullable(),
        }),
        { env },
      ),
    ])
    return {
      ...models,
      sources: sources.items.filter((source) => source.relation),
      sources_truncated: sources.next_cursor !== null,
    }
  })

export const getMetricSource = createServerFn({ method: 'GET' })
  .inputValidator(z.object({ env: environmentSchema, id: z.string().min(1) }))
  .handler(({ data: { env, id } }) =>
    phloApi(
      `api/v1/assets/${encodeURIComponent(id)}?env=${env}`,
      sourceDetailSchema,
      { env },
    ),
  )

export const saveMetricModel = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      definition: metricDefinitionSchema,
      expected_version: z.number().int().nonnegative(),
    }),
  )
  .handler(({ data: { env, definition, expected_version } }) =>
    phloApi(
      expected_version
        ? `api/v1/metric-models/${encodeURIComponent(definition.model_id)}?env=${env}`
        : `api/v1/metric-models?env=${env}`,
      modelSchema,
      {
        env,
        method: expected_version ? 'PUT' : 'POST',
        body: { ...definition, expected_version },
      },
    ),
  )

export const getMetricPreviewSql = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      id: z.string().min(1),
      group_by: z.array(identifier).max(10),
    }),
  )
  .handler(({ data: { env, id, group_by } }) =>
    phloApi(
      `api/v1/metric-models/${encodeURIComponent(id)}/preview-sql?env=${env}`,
      z.object({
        env: environmentSchema,
        model_id: z.string(),
        version: z.number(),
        sql: z.string(),
        source: z.literal('draft_metric_definition'),
      }),
      { env, method: 'POST', body: { group_by } },
    ),
  )
