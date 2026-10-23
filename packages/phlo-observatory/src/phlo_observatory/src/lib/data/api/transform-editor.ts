/** Provider-backed immutable SQL drafts and actual read-only engine plans. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { PhloApiError, environmentSchema, phloApi } from './client'
import {
  archiveMetadataSchema,
  candidateEvidenceSchema,
  reconciliationSchema,
  sourceHandoffSchema,
} from './candidate-evidence'

const modelInput = z.object({
  env: environmentSchema,
  model: z.string().regex(/^[A-Za-z_][A-Za-z0-9_.]{0,255}$/),
})
const sourceSchema = z.object({
  env: environmentSchema,
  model: z.string(),
  path: z.string(),
  project_path: z.string().nullable().optional(),
  sql: z.string(),
  source_digest: z.string(),
  workspace_digest: z.string(),
  model_version: z.string(),
})
const draftSchema = archiveMetadataSchema.extend({
  env: environmentSchema,
  ref: z.string(),
  model: z.string(),
  path: z.string(),
  sql: z.string(),
  draft_id: z.string(),
  revision: z.number().int().positive(),
  source_digest: z.string(),
  workspace_digest: z.string(),
  revision_digest: z.string(),
  sql_digest: z.string(),
  created_at: z.string(),
})
const draftIdentity = modelInput.extend({
  draft_id: z.string().uuid(),
  revision: z.number().int().positive(),
})
const compilationSchema = z.object({
  env: environmentSchema,
  draft_id: z.string(),
  revision: z.number().int(),
  revision_digest: z.string(),
  ref: z.string(),
  status: z.enum(['ready', 'failed']),
  diagnostics: z.array(z.record(z.string(), z.json())),
  plan_id: z.string().nullable(),
  compiled_sql: z.string().nullable(),
  target: z.string().nullable(),
  sql_hash: z.string().nullable(),
  desired_version: z.string().nullable(),
  materialization_available: z.literal(false),
  source: z.literal('phlo-transform-engine'),
})
export type TransformSource = z.infer<typeof sourceSchema>
export type TransformDraft = z.infer<typeof draftSchema>
export type TransformCompilation = z.infer<typeof compilationSchema>

export const getTransformSource = createServerFn({ method: 'GET' })
  .inputValidator(modelInput)
  .handler(({ data: { env, model } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/source?env=${env}`,
      sourceSchema,
      { env },
    ),
  )
export const saveTransformDraft = createServerFn({ method: 'POST' })
  .inputValidator(
    modelInput.extend({
      sql: z
        .string()
        .min(1)
        .max(256 * 1024),
      source_digest: z.string().regex(/^[a-f0-9]{64}$/),
      workspace_digest: z.string().regex(/^[a-f0-9]{64}$/),
      draft_id: z.string().uuid().nullable().optional(),
      expected_revision: z.number().int().nonnegative(),
    }),
  )
  .handler(({ data: { env, model, ...body } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts?env=${env}`,
      draftSchema,
      { env, method: 'POST', body },
    ),
  )
export const getTransformDraft = createServerFn({ method: 'GET' })
  .inputValidator(draftIdentity)
  .handler(({ data: { env, model, draft_id, revision } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}?env=${env}`,
      draftSchema,
      { env },
    ),
  )
export const compileTransformDraft = createServerFn({ method: 'POST' })
  .inputValidator(draftIdentity)
  .handler(({ data: { env, model, draft_id, revision } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}/compile?env=${env}`,
      compilationSchema,
      { env, method: 'POST', body: {} },
    ),
  )

const executionSchema = archiveMetadataSchema.extend({
  execution_id: z.string(),
  model: z.string(),
  env: environmentSchema,
  ref: z.string(),
  draft_id: z.string(),
  revision: z.number().int().positive(),
  revision_digest: z.string(),
  status: z.enum(['prepared', 'running', 'passed', 'failed', 'unknown']),
  created_at: z.string(),
  candidate_ref: z.string().nullable(),
  candidate_catalog: z.string().nullable(),
  candidate_binding: z.record(z.string(), z.json()).nullable(),
  candidate_result_hash: z.string().nullable(),
  candidate_snapshot_id: z.string().nullable(),
  report: z.record(z.string(), z.json()).nullable(),
  preview: z.record(z.string(), z.json()).nullable(),
  evidence: candidateEvidenceSchema.nullable().optional(),
  reconciliation: reconciliationSchema.nullable().optional(),
})
export type TransformExecution = z.infer<typeof executionSchema>
export const executeTransformDraft = createServerFn({ method: 'POST' })
  .inputValidator(
    draftIdentity.extend({
      revision_digest: z.string().regex(/^[a-f0-9]{64}$/),
      base_hash: z.string().regex(/^[a-fA-F0-9]{8,128}$/),
      idempotency_key: z.string().min(1).max(200),
    }),
  )
  .handler(
    async ({
      data: { env, model, draft_id, revision, idempotency_key, ...body },
    }) => {
      try {
        return await phloApi(
          `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}/executions?env=${env}`,
          executionSchema,
          {
            env,
            method: 'POST',
            body,
            headers: { 'Idempotency-Key': idempotency_key },
          },
        )
      } catch (error) {
        if (
          error instanceof PhloApiError &&
          error.reasonCode === 'rejected_before_write'
        )
          return {
            rejected_before_write: true as const,
            message:
              'Compilation was rejected before any candidate write. Repair prerequisites and run again with a fresh request.',
          }
        throw error
      }
    },
  )
export const getTransformExecutions = createServerFn({ method: 'GET' })
  .inputValidator(
    draftIdentity.extend({ include_archived: z.boolean().optional() }),
  )
  .handler(({ data: { env, model, draft_id, revision, include_archived } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}/executions?env=${env}${include_archived ? '&include_archived=true' : ''}`,
      z.object({ env: environmentSchema, items: z.array(executionSchema) }),
      { env },
    ),
  )

const draftSummarySchema = archiveMetadataSchema.extend({
  draft_id: z.string(),
  revision: z.number().int().positive(),
  model: z.string(),
  env: environmentSchema,
  ref: z.string(),
  path: z.string(),
  created_at: z.string(),
  source_digest: z.string(),
  revision_digest: z.string(),
  sql_digest: z.string(),
})
export type TransformDraftSummary = z.infer<typeof draftSummarySchema>
const comparisonSchema = z.object({
  env: environmentSchema,
  draft_id: z.string(),
  revision: z.number().int().positive(),
  revision_digest: z.string(),
  source_digest: z.string(),
  source_path: z.string(),
  base_kind: z.literal('workspace_source'),
  baseline_matches_saved_source: z.boolean(),
  added_lines: z.number().int().nonnegative(),
  removed_lines: z.number().int().nonnegative(),
  unified_diff: z.array(z.string()),
})
export type TransformDraftComparison = z.infer<typeof comparisonSchema>
export const getTransformDrafts = createServerFn({ method: 'GET' })
  .inputValidator(
    modelInput.extend({ include_archived: z.boolean().optional() }),
  )
  .handler(({ data: { env, model, include_archived } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts?env=${env}${include_archived ? '&include_archived=true' : ''}`,
      z.object({
        env: environmentSchema,
        items: z.array(draftSummarySchema),
        total: z.number().int().nonnegative(),
        truncated: z.boolean(),
      }),
      { env },
    ),
  )
export const compareTransformDraft = createServerFn({ method: 'GET' })
  .inputValidator(draftIdentity)
  .handler(({ data: { env, model, draft_id, revision } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}/compare?env=${env}`,
      comparisonSchema,
      { env },
    ),
  )

const archiveInput = z.object({
  archived: z.boolean(),
  reason: z.string().trim().min(1).max(1000),
})
export const archiveTransformDraft = createServerFn({ method: 'POST' })
  .inputValidator(draftIdentity.extend(archiveInput.shape))
  .handler(({ data: { env, model, draft_id, revision, ...body } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}/archive?env=${env}`,
      draftSchema,
      { env, method: 'POST', body },
    ),
  )
export const getTransformHandoff = createServerFn({ method: 'GET' })
  .inputValidator(
    draftIdentity.extend({ execution_id: z.string().uuid().optional() }),
  )
  .handler(({ data: { env, model, draft_id, revision, execution_id } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}/handoff?env=${env}${execution_id ? `&execution_id=${execution_id}` : ''}`,
      sourceHandoffSchema,
      { env },
    ),
  )
const executionIdentity = draftIdentity.extend({
  execution_id: z.string().uuid(),
})
export const archiveTransformExecution = createServerFn({ method: 'POST' })
  .inputValidator(executionIdentity.extend(archiveInput.shape))
  .handler(
    ({ data: { env, model, draft_id, revision, execution_id, ...body } }) =>
      phloApi(
        `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}/executions/${execution_id}/archive?env=${env}`,
        executionSchema,
        { env, method: 'POST', body },
      ),
  )
export const reconcileTransformExecution = createServerFn({ method: 'POST' })
  .inputValidator(executionIdentity)
  .handler(({ data: { env, model, draft_id, revision, execution_id } }) =>
    phloApi(
      `api/v1/transform/models/${encodeURIComponent(model)}/drafts/${draft_id}/${revision}/executions/${execution_id}/reconcile?env=${env}`,
      executionSchema,
      { env, method: 'POST' },
    ),
  )
