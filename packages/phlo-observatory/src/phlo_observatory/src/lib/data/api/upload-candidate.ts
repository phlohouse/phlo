/** Real reviewed CSV destination, candidate plan and confirmed one-off load. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

const identity = z.object({
  env: environmentSchema,
  upload_id: z.string().regex(/^[a-f0-9]{32}$/),
})
const receiptSchema = z.object({
  upload_id: z.string(),
  env: environmentSchema,
  actor: z.string(),
  review_version: z.number().int(),
  checksum: z.string(),
  recipe_digest: z.string(),
  table: z.string(),
  relation: z.string(),
  asset_id: z.string(),
  candidate_ref: z.string(),
  base_ref: z.string(),
  base_hash: z.string(),
  candidate_hash: z.string(),
  launch_ref_hash: z.string().nullable(),
  status: z.enum([
    'preparing',
    'prepared',
    'planned',
    'queued',
    'running',
    'failed',
    'loaded',
  ]),
  contract_digest: z.string().nullable(),
  source_digest: z.string().nullable(),
  plan_hash: z.string().nullable(),
  job_name: z.string().nullable(),
  job_snapshot_id: z.string().nullable(),
  run_id: z.string().nullable(),
  repository_name: z.string().nullable(),
  repository_location: z.string().nullable(),
  snapshot_id: z.string().nullable(),
  row_count: z.number().int().nullable(),
  loaded: z.boolean(),
  main_unchanged: z.boolean().nullable(),
  files: z.array(z.string()),
  candidate_binding: z.record(z.string(), z.json()),
  attempts: z.array(
    z.object({
      run_id: z.string(),
      status: z.string(),
      plan_hash: z.string(),
      job_snapshot_id: z.string(),
      launch_ref_hash: z.string(),
    }),
  ),
})
const previewSchema = z.object({
  receipt: receiptSchema,
  columns: z.array(z.string()),
  column_types: z.array(z.string()),
  rows: z.array(z.record(z.string(), z.json())),
  has_more: z.boolean(),
  row_count: z.number().int(),
  snapshot_id: z.string(),
  ref_hash: z.string(),
  query_id: z.string().nullable(),
})
export type UploadCandidate = z.infer<typeof receiptSchema>
export type UploadCandidatePreview = z.infer<typeof previewSchema>

export const prepareUploadDestination = createServerFn({ method: 'POST' })
  .inputValidator(
    identity.extend({
      expected_version: z.number().int().positive(),
      table: z.string().regex(/^[a-z][a-z0-9_]{0,62}$/),
    }),
  )
  .handler(({ data: { env, upload_id, ...body } }) =>
    phloApi(
      `api/v1/uploads/csv/${upload_id}/destination?env=${encodeURIComponent(env)}`,
      receiptSchema,
      { env, method: 'POST', body },
    ),
  )
export const getUploadCandidate = createServerFn({ method: 'GET' })
  .inputValidator(identity)
  .handler(({ data: { env, upload_id } }) =>
    phloApi(
      `api/v1/uploads/csv/${upload_id}/candidate?env=${encodeURIComponent(env)}`,
      receiptSchema,
      { env },
    ),
  )
export const planUploadCandidate = createServerFn({ method: 'POST' })
  .inputValidator(identity)
  .handler(({ data: { env, upload_id } }) =>
    phloApi(
      `api/v1/uploads/csv/${upload_id}/candidate/plan?env=${encodeURIComponent(env)}`,
      receiptSchema,
      { env, method: 'POST', body: {} },
    ),
  )
export const loadUploadCandidate = createServerFn({ method: 'POST' })
  .inputValidator(
    identity.extend({
      expected_version: z.number().int().positive(),
      recipe_digest: z.string(),
      plan_hash: z.string(),
      confirmed: z.literal(true),
      idempotency_key: z.string().min(8).max(128),
      retry_of_run_id: z.string().nullable().optional(),
    }),
  )
  .handler(({ data: { env, upload_id, ...body } }) =>
    phloApi(
      `api/v1/uploads/csv/${upload_id}/candidate/load?env=${encodeURIComponent(env)}`,
      receiptSchema,
      { env, method: 'POST', body },
    ),
  )
export const previewUploadCandidate = createServerFn({ method: 'GET' })
  .inputValidator(identity)
  .handler(({ data: { env, upload_id } }) =>
    phloApi(
      `api/v1/uploads/csv/${upload_id}/candidate/preview?env=${encodeURIComponent(env)}`,
      previewSchema,
      { env },
    ),
  )
