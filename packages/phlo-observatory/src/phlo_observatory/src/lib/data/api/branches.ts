/** Validates and executes branch comparison, checking, and lifecycle operations. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'
import { runStatusSchema } from './pipelines'

const refSchema = z.object({
  env: environmentSchema,
  name: z.string(),
  type: z.enum(['BRANCH', 'TAG']),
  hash: z.string(),
  protected: z.boolean(),
})
const refsSchema = z.object({
  env: environmentSchema,
  items: z.array(refSchema),
})
export const wapPageSchema = z.object({
  env: environmentSchema,
  items: z.array(
    z.object({
      logical_run_id: z.string().min(1),
      run_id: z.string().min(1),
      job_id: z.string().min(1),
      status: runStatusSchema,
      created_at: z.iso.datetime({ offset: true }),
      staging_ref: z.string().min(1),
      catalog_system: z.string().nullable(),
      strategy: z.enum(['branch', 'snapshot', 'unknown']),
      branch_state: z.enum(['present', 'absent', 'unknown', 'not_applicable']),
      branch_hash: z.string().nullable(),
      report_state: z.enum(['verified', 'missing', 'invalid']),
      lifecycle_status: z.string().nullable(),
      reported_at: z.iso.datetime({ offset: true }).nullable(),
    }),
  ),
  scanned_runs: z.number().int().nonnegative(),
  scan_limit: z.number().int().positive(),
  scan_limited: z.boolean(),
  catalog_available: z.boolean(),
})
const commitSchema = z.object({
  hash: z.string(),
  parent_hashes: z.array(z.string()),
  message: z.string().nullable(),
  author: z.string().nullable(),
  committer: z.string().nullable(),
  committed_at: z.string().nullable(),
})
const commitsSchema = z.object({
  env: environmentSchema,
  branch: z.string(),
  items: z.array(commitSchema),
  next_cursor: z.string().nullable(),
})
const diffSchema = z.object({
  env: environmentSchema,
  source: z.string(),
  target: z.string(),
  source_hash: z.string(),
  target_hash: z.string(),
  items: z.array(
    z.object({
      key: z.string(),
      status: z.enum(['added', 'modified', 'deleted']),
      from_content_id: z.string().nullable(),
      to_content_id: z.string().nullable(),
    }),
  ),
  truncated: z.boolean(),
})
const compareSchema = z.object({
  env: environmentSchema,
  source: z.string(),
  target: z.string(),
  source_hash: z.string(),
  target_hash: z.string(),
  merge_base: z.string().nullable(),
  ahead: z.number().int().nonnegative().nullable(),
  behind: z.number().int().nonnegative().nullable(),
  status: z.enum(['compared', 'unavailable']),
})
const checkSchema = z.object({
  name: z.enum(['tests', 'contracts', 'audits']),
  status: z.enum(['passed', 'failed', 'unavailable']),
  run_id: z.string().nullable(),
  message: z.string().nullable(),
})
const checksSchema = z.object({
  env: environmentSchema,
  branch: z.string(),
  branch_hash: z.string(),
  items: z.array(checkSchema),
  passed: z.boolean(),
  status: z.enum(['succeeded', 'rejected']),
})
const actionSchema = z.object({
  env: environmentSchema,
  operation: z.string(),
  branch: z.string(),
  target: z.string().nullable(),
  status: z.enum(['succeeded', 'conflict', 'rejected']),
  source_hash: z.string().nullable(),
  target_hash: z.string().nullable(),
  resulting_hash: z.string().nullable(),
  details: z.record(z.string(), z.json()),
})
const signatureSchema = z.object({ signature_id: z.string() }).passthrough()

export type BranchRef = z.infer<typeof refSchema>
export type BranchCommit = z.infer<typeof commitSchema>
export type BranchDiff = z.infer<typeof diffSchema>
export type BranchComparison = z.infer<typeof compareSchema>
export type BranchCheck = z.infer<typeof checkSchema>
export type BranchAction = z.infer<typeof actionSchema>

const envInput = z.object({ env: environmentSchema })
const detailInput = envInput.extend({
  branch: z.string().min(1),
  target: z.string().min(1),
})
const mutationInput = z.object({
  env: environmentSchema,
  idempotencyKey: z.string().min(1),
  confirmed: z.literal(true),
})
const branchActionTimeoutMs = 120_000

export const getBranchesPage = createServerFn({ method: 'GET' })
  .validator(envInput)
  .handler(async ({ data: { env } }) => {
    const refs = await phloApi(`/api/v1/branches/refs?env=${env}`, refsSchema, {
      env,
    })
    return {
      env,
      branches: refs.items.filter((item) => item.type === 'BRANCH'),
      tags: refs.items.filter((item) => item.type === 'TAG'),
    }
  })

export const getWapRuns = createServerFn({ method: 'GET' })
  .validator(envInput)
  .handler(({ data: { env } }) =>
    phloApi(`/api/v1/wap/runs?env=${env}`, wapPageSchema, { env }),
  )

export const getBranchDetail = createServerFn({ method: 'GET' })
  .validator(detailInput)
  .handler(async ({ data }) => {
    const branch = encodeURIComponent(data.branch),
      target = encodeURIComponent(data.target),
      query = `env=${data.env}&target=${target}`
    const [ref, commits, diff, comparison] = await Promise.all([
      phloApi(`/api/v1/branches/${branch}?env=${data.env}`, refSchema, {
        env: data.env,
      }),
      phloApi(
        `/api/v1/branches/${branch}/commits?env=${data.env}&limit=100`,
        commitsSchema,
        { env: data.env },
      ),
      phloApi(`/api/v1/branches/${branch}/diff?${query}`, diffSchema, {
        env: data.env,
      }),
      phloApi(`/api/v1/branches/${branch}/compare?${query}`, compareSchema, {
        env: data.env,
      }),
    ])
    return { ref, commits, diff, comparison }
  })

export const createBranch = createServerFn({ method: 'POST' })
  .validator(
    mutationInput.extend({
      name: z.string().min(1),
      fromRef: z.string().min(1),
    }),
  )
  .handler(({ data }) =>
    phloApi(`/api/v1/branches?env=${data.env}`, actionSchema, {
      env: data.env,
      method: 'POST',
      idempotencyKey: data.idempotencyKey,
      body: { name: data.name, from_ref: data.fromRef },
    }),
  )

export const deleteBranch = createServerFn({ method: 'POST' })
  .validator(
    mutationInput.extend({
      branch: z.string().min(1),
      expectedHash: z.string().min(1),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `/api/v1/branches/${encodeURIComponent(data.branch)}?env=${data.env}`,
      actionSchema,
      {
        env: data.env,
        method: 'DELETE',
        idempotencyKey: data.idempotencyKey,
        body: { expected_hash: data.expectedHash },
      },
    ),
  )

export const runBranchChecks = createServerFn({ method: 'POST' })
  .validator(
    mutationInput.extend({
      branch: z.string().min(1),
      expectedHash: z.string().min(1),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `/api/v1/branches/${encodeURIComponent(data.branch)}/checks?env=${data.env}`,
      checksSchema,
      {
        env: data.env,
        method: 'POST',
        idempotencyKey: data.idempotencyKey,
        timeoutMs: branchActionTimeoutMs,
        body: { expected_hash: data.expectedHash },
      },
    ),
  )

export const rebaseBranch = createServerFn({ method: 'POST' })
  .validator(
    mutationInput.extend({
      branch: z.string().min(1),
      target: z.string().min(1),
      sourceHash: z.string().min(1),
      targetHash: z.string().min(1),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `/api/v1/branches/${encodeURIComponent(data.branch)}/rebase?env=${data.env}`,
      actionSchema,
      {
        env: data.env,
        method: 'POST',
        idempotencyKey: data.idempotencyKey,
        timeoutMs: branchActionTimeoutMs,
        body: {
          target: data.target,
          expected_source_hash: data.sourceHash,
          expected_target_hash: data.targetHash,
        },
      },
    ),
  )

const mergeInput = mutationInput.extend({
  branch: z.string().min(1),
  target: z.string().min(1),
  sourceHash: z.string().min(1),
  targetHash: z.string().min(1),
  message: z.string().min(1).max(1000),
  incidentId: z.string().optional(),
})
export const trialMerge = createServerFn({ method: 'POST' })
  .validator(mergeInput)
  .handler(({ data }) =>
    phloApi(
      `/api/v1/branches/${encodeURIComponent(data.branch)}/trial-merge?env=${data.env}`,
      actionSchema,
      {
        env: data.env,
        method: 'POST',
        idempotencyKey: data.idempotencyKey,
        timeoutMs: branchActionTimeoutMs,
        body: {
          target: data.target,
          expected_source_hash: data.sourceHash,
          expected_target_hash: data.targetHash,
          message: data.message,
          incident_id: data.incidentId,
        },
      },
    ),
  )

export const signAndMerge = createServerFn({ method: 'POST' })
  .validator(
    mergeInput.extend({
      signatureTargetVersion: z.string().min(1),
      signatureKey: z.string().min(1),
      mergeKey: z.string().min(1),
    }),
  )
  .handler(async ({ data }) => {
    const signature = await phloApi('/api/v1/signatures', signatureSchema, {
      method: 'POST',
      idempotencyKey: data.signatureKey,
      body: {
        action: 'branch.merge',
        target_type: 'branch',
        target_id: `${data.env}:${data.branch}->${data.target}`,
        target_version: data.signatureTargetVersion,
        meaning: 'approved',
        justification: data.message,
      },
    })
    return phloApi(
      `/api/v1/branches/${encodeURIComponent(data.branch)}/merge?env=${data.env}`,
      actionSchema,
      {
        env: data.env,
        method: 'POST',
        idempotencyKey: data.mergeKey,
        timeoutMs: branchActionTimeoutMs,
        body: {
          target: data.target,
          expected_source_hash: data.sourceHash,
          expected_target_hash: data.targetHash,
          message: data.message,
          incident_id: data.incidentId,
          signature_id: signature.signature_id,
        },
      },
    )
  })
