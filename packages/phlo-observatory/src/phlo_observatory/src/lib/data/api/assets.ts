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
  reports: z.array(z.string()).optional(),
  layer: z.enum(['bronze', 'silver', 'gold']).nullable().optional(),
  freshness_sla_seconds: z.number().nullable().optional(),
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
  column_lineage: z
    .record(
      z.string(),
      z.array(
        z.object({ asset_key: z.array(z.string()), column_name: z.string() }),
      ),
    )
    .nullable()
    .optional(),
  downstream: z.array(assetSchema).optional(),
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
  sql: z.string(),
})
const snapshotIdSchema = z
  .union([z.string().regex(/^-?\d+$/), z.number().int().safe()])
  .transform(String)
const snapshotsSchema = z.object({
  env: environmentSchema,
  nessie_ref: z.string(),
  table_name: z.string().optional(),
  current_snapshot_id: snapshotIdSchema.nullable().optional(),
  metadata_location: z.string().nullable().optional(),
  items: z.array(
    z.object({
      snapshot_id: snapshotIdSchema,
      timestamp_ms: z.number(),
      operation: z.string().nullable(),
      summary: z.record(z.string(), z.string()),
      parent_id: snapshotIdSchema.nullable().optional(),
      schema_id: z.number().nullable().optional(),
      author: z.string().nullable().optional(),
      sequence_number: z.number().nullable().optional(),
      manifest_list: z.string().nullable().optional(),
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
  rules: z.array(auditRuleSchema),
  failure_policy: z.enum(['block', 'warn']),
  schema_digest: z.string().nullable(),
  table_relation: z.string().nullable(),
})
export type ApiAsset = z.infer<typeof assetSchema>
export type ApiAssetDetail = z.infer<typeof assetDetailSchema>
export type AssetPreview = z.infer<typeof previewSchema>
export type AssetSnapshots = z.infer<typeof snapshotsSchema>
export type AssetSchemaHistory = z.infer<typeof schemaHistorySchema>
export type AssetChecks = z.infer<typeof checksSchema>
export type AuditRule = z.infer<typeof auditRuleSchema>
export type AuditProposal = z.infer<typeof auditProposalSchema>

const auditTestSchema = z.object({
  proposal_id: z.string(),
  source_digest: z.string(),
  env: environmentSchema,
  nessie_ref: z.string(),
  engine: z.literal('trino'),
  executed_at: z.string(),
  rows_checked: z.number().int().min(0).max(100),
  sampled: z.boolean(),
  passed: z.boolean(),
  results: z.array(
    z.object({
      kind: z.enum(['not_null', 'unique', 'range']),
      column: z.string(),
      passed: z.boolean(),
      failure_count: z.number().int().nonnegative(),
      failure_message: z.string().nullable(),
    }),
  ),
})
export type AuditTestResult = z.infer<typeof auditTestSchema>
const auditProposalRequest = z.object({
  env: environmentSchema,
  id: z.string().min(1),
  proposal_id: z.string().regex(/^[a-f0-9]{32}$/),
})

export const previewFilterSchema = z.union([
  z
    .object({
      column: z.string().min(1).max(256),
      operator: z.enum(['eq', 'ne', 'lt', 'lte', 'gt', 'gte']),
      value: z.union([
        z.string().max(2048),
        z
          .number()
          .finite()
          .refine(
            (value) => !Number.isInteger(value) || Number.isSafeInteger(value),
            'Use a safely representable number.',
          ),
        z.boolean(),
      ]),
    })
    .strict(),
  z
    .object({
      column: z.string().min(1).max(256),
      operator: z.enum(['is_null', 'is_not_null']),
    })
    .strict(),
])
export type PreviewFilter = z.infer<typeof previewFilterSchema>

export const getAssetPreview = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      id: z.string().min(1),
      filters: z.array(previewFilterSchema).max(20),
    }),
  )
  .handler(({ data: { env, id, filters } }) =>
    phloApi(
      `api/v1/assets/${encodeURIComponent(id)}/preview?env=${env}&limit=20&filters=${encodeURIComponent(JSON.stringify(filters))}`,
      previewSchema,
      { env },
    ),
  )

export const rollbackAssetSnapshot = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      table_name: z.string().min(1),
      snapshot_id: z.string().regex(/^[0-9]{1,19}$/),
      expected_metadata_location: z.string().min(1),
      nessie_ref: z.string().min(1),
      confirmed: z.literal(true),
      idempotency_key: z.string().uuid(),
    }),
  )
  .handler(({ data: { env, table_name, ...body } }) =>
    phloApi(
      `api/v1/tables/${encodeURIComponent(table_name)}/rollback?env=${env}`,
      z.object({
        env: environmentSchema,
        table_name: z.string(),
        nessie_ref: z.string(),
        rolled_back_to: z.string(),
      }),
      { env, body },
    ),
  )

export function assetLayer(
  asset: Pick<ApiAsset, 'group_name' | 'key' | 'layer'>,
) {
  if (asset.layer) return asset.layer
  return (['bronze', 'silver', 'gold'] as const).find(
    (layer) =>
      asset.group_name?.toLowerCase() === layer ||
      asset.key[0]?.toLowerCase() === layer,
  )
}

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

export const materializationInputSchema = z
  .object({
    env: environmentSchema,
    id: z.string().min(1),
    job_name: z.string().min(1),
    mode: z.enum(['latest', 'backfill', 'full']),
    from_time: z.iso.datetime({ offset: true }).optional(),
    to_time: z.iso.datetime({ offset: true }).optional(),
    write_ref: z.string().min(1).optional(),
    rebuild_downstream: z.boolean(),
  })
  .superRefine((value, ctx) => {
    if (value.mode === 'backfill') {
      if (
        !value.from_time ||
        !value.to_time ||
        Date.parse(value.from_time) >= Date.parse(value.to_time)
      )
        ctx.addIssue({
          code: 'custom',
          message: 'Supply an increasing UTC time range.',
        })
    } else if (value.from_time || value.to_time) {
      ctx.addIssue({
        code: 'custom',
        message: 'Only backfills accept a time range.',
      })
    }
  })
const materializationEstimateSchema = z.object({
  env: environmentSchema,
  asset_id: z.string(),
  partition_count: z.number().int().positive(),
  plan_hash: z.string().nullable(),
  nessie_ref: z.string().nullable(),
  ref_hash: z.string().nullable(),
  job_snapshot_id: z.string().nullable(),
  selected_assets: z.array(z.string()),
  partition_keys: z.array(z.string()),
  estimated_rows: z.null(),
  estimated_cost: z.null(),
  estimated_bytes: z.null(),
  estimated_duration_seconds: z.null(),
  cost_status: z.string(),
  workload_status: z.string(),
})
export type MaterializationEstimate = z.infer<
  typeof materializationEstimateSchema
>
export type MaterializationInput = z.infer<typeof materializationInputSchema>

export const getMaterializationEstimate = createServerFn({ method: 'GET' })
  .inputValidator(materializationInputSchema)
  .handler(({ data: { id, ...data } }) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(data))
      if (value !== undefined) query.set(key, String(value))
    return phloApi(
      `api/v1/assets/${encodeURIComponent(id)}/materialization-estimate?${query}`,
      materializationEstimateSchema,
      { env: data.env, timeoutMs: 120_000 },
    )
  })

export const materializeAsset = createServerFn({ method: 'POST' })
  .inputValidator(
    materializationInputSchema.safeExtend({
      idempotency_key: z.string().uuid(),
      plan_hash: z.string().min(1),
      confirmed: z.literal(true),
    }),
  )
  .handler(({ data: { env, id, ...body } }) =>
    phloApi(
      `api/v1/assets/${encodeURIComponent(id)}/materialize?env=${env}`,
      z.object({
        env: environmentSchema,
        nessie_ref: z.string(),
        result: z.object({
          accepted: z.boolean(),
          run_ids: z.array(z.string()),
          runs: z.array(
            z.object({ accepted: z.boolean(), message: z.string().optional() }),
          ),
        }),
      }),
      { env, body: { ...body, dry_run: false }, timeoutMs: 120_000 },
    ),
  )

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
      failure_policy: z.enum(['block', 'warn']),
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
          failure_policy: data.failure_policy,
          idempotency_key: data.idempotency_key,
        },
      },
    ),
  )

export const getAuditProposal = createServerFn({ method: 'GET' })
  .inputValidator(auditProposalRequest)
  .handler(({ data }) =>
    phloApi(
      `api/v1/assets/${encodeURIComponent(data.id)}/audits/${data.proposal_id}?env=${data.env}`,
      auditProposalSchema,
      { env: data.env },
    ),
  )

export const testAuditProposal = createServerFn({ method: 'POST' })
  .inputValidator(auditProposalRequest)
  .handler(({ data }) =>
    phloApi(
      `api/v1/assets/${encodeURIComponent(data.id)}/audits/${data.proposal_id}/test?env=${data.env}`,
      auditTestSchema,
      { env: data.env, method: 'POST', timeoutMs: 30000 },
    ),
  )

export const publishAuditProposal = createServerFn({ method: 'POST' })
  .inputValidator(
    auditProposalRequest.extend({
      expected_source_digest: z.string().regex(/^[a-f0-9]{64}$/),
      idempotency_key: z.string().uuid(),
      confirmed: z.literal(true),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `api/v1/assets/${encodeURIComponent(data.id)}/audits/${data.proposal_id}/pull-request?env=${data.env}`,
      z.object({
        proposal_id: z.string(),
        status: z.literal('pending_review'),
        repository: z.string(),
        base_branch: z.string(),
        head_branch: z.string(),
        pull_request_number: z.number().int().positive(),
        pull_request_url: z
          .url()
          .refine((url) => new URL(url).origin === 'https://github.com'),
      }),
      {
        env: data.env,
        timeoutMs: 60000,
        body: {
          expected_source_digest: data.expected_source_digest,
          idempotency_key: data.idempotency_key,
        },
      },
    ),
  )
