/** Validates and executes staging checks, resynchronisation, and promotion operations. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

const stagingEnvironmentSchema = z.literal('staging')
const candidateSchema = z.object({
  env: stagingEnvironmentSchema,
  candidate_id: z.string(),
  prod_git_revision: z.string(),
  staging_git_revision: z.string(),
  prod_ref: z.string(),
  prod_hash: z.string(),
  staging_ref: z.string(),
  staging_hash: z.string(),
  dagster_location: z.string(),
  staging_location: z.string(),
  code_paths: z.array(z.string()),
  code_changes: z.array(z.string()),
  jobs: z.object({ prod: z.array(z.string()), staging: z.array(z.string()) }),
  copy_inventory: z.object({
    prod: z.array(z.string()),
    staging: z.array(z.string()),
  }),
  observed_at: z.string(),
})
const checkSchema = z.object({
  name: z.string(),
  status: z.enum(['passed', 'failed', 'unavailable']),
  run_id: z.string().nullable(),
  message: z.string().nullable().optional(),
})
const checksSchema = z.object({
  env: stagingEnvironmentSchema,
  candidate_id: z.string(),
  items: z.array(checkSchema),
  passed: z.boolean(),
})
const historySchema = z.object({
  env: stagingEnvironmentSchema,
  items: z.array(
    z.object({
      timestamp: z.string(),
      operation: z.string(),
      target: z.string(),
      subject: z.string(),
      dry_run: z.boolean(),
      result: z.object({ status: z.string().optional() }),
    }),
  ),
})
const actionSchema = z.object({
  env: stagingEnvironmentSchema,
  operation: z.string(),
  status: z.string(),
  resulting_hash: z.string().optional(),
})
const signatureSchema = z.object({ signature_id: z.string() })

export type StagingCandidate = z.infer<typeof candidateSchema>
export type StagingChecks = z.infer<typeof checksSchema>
export type StagingHistory = z.infer<typeof historySchema>

const envInput = z.object({ env: environmentSchema })
const mutationInput = z.object({
  candidateId: z.string().min(1),
  idempotencyKey: z.string().min(1),
})
const timeoutMs = 120_000

export const getStagingOverview = createServerFn({ method: 'GET' })
  .validator(envInput)
  .handler(async ({ data }) => {
    if (data.env !== 'staging') return { kind: 'prod' as const, env: data.env }
    const [candidate, history] = await Promise.all([
      phloApi(
        '/api/v1/staging/promotions/candidate?env=staging',
        candidateSchema,
        { env: 'staging' },
      ),
      phloApi('/api/v1/staging/promotions?env=staging', historySchema, {
        env: 'staging',
      }),
    ])
    return { kind: 'staging' as const, env: data.env, candidate, history }
  })

export const runStagingChecks = createServerFn({ method: 'POST' })
  .validator(mutationInput)
  .handler(({ data }) =>
    phloApi(
      '/api/v1/staging/promotions/candidate/checks?env=staging',
      checksSchema,
      {
        env: 'staging',
        method: 'POST',
        idempotencyKey: data.idempotencyKey,
        timeoutMs,
        body: { candidate_id: data.candidateId },
      },
    ),
  )

export const resyncStaging = createServerFn({ method: 'POST' })
  .validator(
    z.object({
      expectedProdHash: z.string().min(1),
      expectedStagingHash: z.string().min(1),
      idempotencyKey: z.string().min(1),
      confirm: z.literal(true),
    }),
  )
  .handler(({ data }) =>
    phloApi('/api/v1/staging/resync?env=staging', actionSchema, {
      env: 'staging',
      method: 'POST',
      idempotencyKey: data.idempotencyKey,
      body: {
        expected_prod_hash: data.expectedProdHash,
        expected_staging_hash: data.expectedStagingHash,
        confirm: true,
      },
    }),
  )

export const promoteStaging = createServerFn({ method: 'POST' })
  .validator(
    mutationInput.extend({
      prodRef: z.string().min(1),
      stagingRef: z.string().min(1),
      justification: z.string().min(1).max(1000),
      signatureKey: z.string().min(1),
      confirm: z.literal(true),
    }),
  )
  .handler(async ({ data }) => {
    const signature = await phloApi('/api/v1/signatures', signatureSchema, {
      method: 'POST',
      idempotencyKey: `signature:${data.signatureKey}`,
      body: {
        action: 'staging.promote',
        target_type: 'promotion',
        target_id: `${data.prodRef}<-${data.stagingRef}`,
        target_version: data.candidateId,
        meaning: 'approved',
        justification: data.justification,
      },
    })
    return phloApi('/api/v1/staging/promotions?env=staging', actionSchema, {
      env: 'staging',
      method: 'POST',
      idempotencyKey: data.idempotencyKey,
      timeoutMs,
      body: {
        candidate_id: data.candidateId,
        signature_id: signature.signature_id,
        justification: data.justification,
        confirm: true,
      },
    })
  })
