/** Validates and executes asset discovery, inspection, and mutation operations. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'
import { jobsSchema } from './pipelines'

const assetSchema = z.object({
  id: z.string(),
  key: z.array(z.string()),
  description: z.string().nullable(),
  compute_kind: z.string().nullable(),
  group_name: z.string().nullable(),
  is_source: z.boolean(),
  dependencies: z.array(z.array(z.string())),
  last_materialization_at: z.string().nullable(),
  last_run_id: z.string().nullable(),
  relation: z.string().nullable(),
  history_scoped: z.boolean(),
})
const assetDetailSchema = assetSchema.extend({
  columns: z.array(
    z.object({
      name: z.string(),
      type: z.string().nullable(),
      description: z.string().nullable(),
    }),
  ),
  schema_observed_at: z.string().nullable(),
})
const assetPageSchema = z.object({
  env: environmentSchema,
  items: z.array(assetSchema),
  next_cursor: z.string().nullable(),
})
export const assetTabSchema = z.enum([
  'overview',
  'data',
  'schema',
  'lineage',
  'snapshots',
  'audits',
])
const assetRequest = z.object({
  env: environmentSchema,
  id: z.string().min(1),
  tab: assetTabSchema.default('overview'),
})
const previewSchema = z.object({
  env: environmentSchema,
  nessie_ref: z.string(),
  columns: z.array(z.object({ name: z.string(), type: z.string().nullable() })),
  rows: z.array(z.record(z.string(), z.json())),
  has_more: z.boolean(),
})
const snapshotsSchema = z.object({
  env: environmentSchema,
  nessie_ref: z.string(),
  items: z.array(
    z.object({
      snapshot_id: z
        .union([z.string().regex(/^-?\d+$/), z.number().int().safe()])
        .transform(String),
      timestamp_ms: z.number(),
      operation: z.string().nullable(),
      summary: z.record(z.string(), z.string()),
    }),
  ),
})
const schemaHistorySchema = z.object({
  env: environmentSchema,
  nessie_ref: z.string(),
  current_schema_id: z.number(),
  items: z.array(
    z.object({
      schema_id: z.number(),
      fields: z.array(
        z.object({ name: z.string(), type: z.string(), required: z.boolean() }),
      ),
    }),
  ),
})
const checksSchema = z.object({
  env: environmentSchema,
  definitions: z.array(
    z.object({ name: z.string(), description: z.string().nullable() }),
  ),
  executions: z.array(
    z.object({
      status: z.string(),
      run_id: z.string(),
      timestamp: z.string(),
      check_name: z.string(),
      passed: z.boolean().nullable(),
      severity: z.string().nullable(),
    }),
  ),
})
const actionResponseSchema = z.object({
  env: environmentSchema,
  asset_id: z.string(),
  nessie_ref: z.string(),
  result: z.record(z.string(), z.json()),
})
const auditRuleSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('not_null'), column: z.string().min(1) }),
  z.object({ kind: z.literal('unique'), column: z.string().min(1) }),
  z.object({
    kind: z.literal('range'),
    column: z.string().min(1),
    minimum: z.number().finite(),
    maximum: z.number().finite(),
  }),
])
const auditProposalSchema = z.object({
  proposal_id: z.string(),
  env: environmentSchema,
  asset_id: z.string(),
  nessie_ref: z.string(),
  check_name: z.string(),
  file_path: z.string(),
  source_digest: z.string(),
  source: z.string(),
  patch: z.string(),
  status: z.literal('pending_review'),
  created_at: z.string(),
})
export type ApiAsset = z.infer<typeof assetSchema>
export type AuditRule = z.infer<typeof auditRuleSchema>

export const getAssetList = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(({ data: env }) =>
    phloApi(`api/v1/assets?env=${env}`, assetPageSchema, { env }),
  )

export const getAssetDetail = createServerFn({ method: 'GET' })
  .inputValidator(assetRequest)
  .handler(async ({ data: { env, id, tab } }) => {
    const asset = await phloApi(
      `api/v1/assets/${encodeURIComponent(id)}?env=${env}`,
      assetDetailSchema,
    )
    const jobs = await phloApi(`api/v1/jobs?env=${env}`, jobsSchema, { env })
    const matchingJobs = jobs.items.filter((job) =>
      job.selected_assets.some((key) => key.join('/') === asset.key.join('/')),
    )
    const base = { asset, jobs: matchingJobs, env }
    try {
      switch (tab) {
        case 'data':
          return {
            ...base,
            kind: 'data' as const,
            data: await phloApi(
              `api/v1/assets/${encodeURIComponent(id)}/preview?env=${env}&limit=20`,
              previewSchema,
              { env },
            ),
          }
        case 'schema':
          if (!asset.relation)
            return {
              ...base,
              kind: 'unavailable' as const,
              message:
                'No Iceberg table relation has been observed for this asset.',
            }
          return {
            ...base,
            kind: 'schema' as const,
            data: await phloApi(
              `api/v1/tables/${encodeURIComponent(asset.relation)}/schema-history?env=${env}`,
              schemaHistorySchema,
              { env },
            ),
          }
        case 'snapshots':
          if (!asset.relation)
            return {
              ...base,
              kind: 'unavailable' as const,
              message:
                'No Iceberg table relation has been observed for this asset.',
            }
          return {
            ...base,
            kind: 'snapshots' as const,
            data: await phloApi(
              `api/v1/tables/${encodeURIComponent(asset.relation)}/snapshots?env=${env}`,
              snapshotsSchema,
              { env },
            ),
          }
        case 'audits':
          return {
            ...base,
            kind: 'audits' as const,
            data: await phloApi(
              `api/v1/assets/${encodeURIComponent(id)}/checks?env=${env}`,
              checksSchema,
              {
                env,
              },
            ),
          }
        case 'overview':
        case 'lineage':
          return { ...base, kind: tab }
      }
    } catch (error) {
      return {
        ...base,
        kind: 'unavailable' as const,
        message:
          error instanceof Error
            ? error.message
            : 'This asset view is unavailable.',
      }
    }
  })

export const materializeAsset = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      id: z.string().min(1),
      job_name: z.string().min(1),
      idempotency_key: z.string().uuid(),
      confirmed: z.literal(true),
    }),
  )
  .handler(async ({ data: { env, id, job_name, idempotency_key } }) => {
    const response = await phloApi(
      `api/v1/assets/${encodeURIComponent(id)}/materialize?env=${env}`,
      z.object({
        env: environmentSchema,
        nessie_ref: z.string(),
        result: z.object({
          accepted: z.boolean(),
          run_id: z.string().nullable(),
        }),
      }),
      { env, body: { job_name, idempotency_key, dry_run: false } },
    )
    if (!response.result.accepted || !response.result.run_id)
      throw new Error('Dagster did not accept the materialization.')
    return { run_id: response.result.run_id, nessie_ref: response.nessie_ref }
  })

export const backfillAsset = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      id: z.string().min(1),
      job_name: z.string().min(1),
      partition_set_name: z.string().min(1),
      selection: z.enum(['explicit', 'latest', 'all']),
      partitions: z.array(z.string().min(1)).max(500),
      idempotency_key: z.string().uuid(),
      confirmed: z.literal(true),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `api/v1/assets/${encodeURIComponent(data.id)}/backfill?env=${data.env}`,
      actionResponseSchema,
      {
        env: data.env,
        body: {
          job_name: data.job_name,
          partition_set_name: data.partition_set_name,
          selection: data.selection,
          partitions: data.selection === 'explicit' ? data.partitions : [],
          dry_run: false,
          idempotency_key: data.idempotency_key,
        },
      },
    ),
  )

export const createAuditProposal = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      id: z.string().min(1),
      check_name: z.string().regex(/^[A-Za-z][A-Za-z0-9_]{0,63}$/),
      rules: z.array(auditRuleSchema).min(1).max(20),
      idempotency_key: z.string().uuid(),
      confirmed: z.literal(true),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `api/v1/assets/${encodeURIComponent(data.id)}/audits?env=${data.env}`,
      auditProposalSchema,
      {
        env: data.env,
        body: {
          check_name: data.check_name,
          rules: data.rules,
          idempotency_key: data.idempotency_key,
        },
      },
    ),
  )
