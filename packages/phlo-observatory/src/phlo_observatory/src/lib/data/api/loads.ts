/** Authorized load observations preserve receipt and ingestion-run evidence grains. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

const observationSchema = z.object({
  id: z.string(),
  evidence_kind: z.enum(['csv_candidate', 'csv_attempt', 'ingestion_run']),
  name: z.string(),
  source: z.string(),
  asset_id: z.string(),
  relation: z.string().nullable(),
  status: z.string(),
  observed_at: z.string().nullable(),
  rows: z.number().int().nonnegative().nullable(),
  rows_basis: z.string().nullable(),
  byte_count: z.number().int().nonnegative().nullable(),
  checksum: z.string().nullable(),
  run_id: z.string().nullable(),
  job_id: z.string().nullable(),
  ref: z.string(),
  ref_hash: z.string().nullable(),
  snapshot_id: z.string().nullable(),
  upload_id: z.string().nullable(),
  upload_filename: z.string().nullable().optional(),
  upload_created_at: z.string().nullable().optional(),
  upload_actor: z.string().nullable().optional(),
  run_started_at: z.string().nullable().optional(),
  run_ended_at: z.string().nullable().optional(),
  timestamp_basis: z
    .enum([
      'receipt',
      'materialization',
      'run_created',
      'run_started',
      'run_ended',
      'unavailable',
    ])
    .optional(),
  review_version: z.number().int().nullable(),
  recipe_digest: z.string().nullable(),
  detail: z.string(),
})
export const loadsSchema = z.object({
  env: environmentSchema,
  items: z.array(observationSchema),
  observed_at: z.string(),
  truncated: z.boolean(),
  limitations: z.array(z.string()),
  next_cursor: z.string().nullable(),
})
export type LoadObservation = z.infer<typeof observationSchema>
export type LoadsPage = z.infer<typeof loadsSchema>
export const getLoads = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      after: z.string().optional(),
      limit: z.number().int().min(1).max(300).optional(),
    }),
  )
  .handler(({ data: { env, after, limit } }) => {
    const params = new URLSearchParams({ env })
    if (after !== undefined) params.set('after', after)
    if (limit !== undefined) params.set('limit', String(limit))
    return phloApi(`api/v1/loads?${params}`, loadsSchema, { env })
  })
