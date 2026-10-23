/** Durable evidence is distinct from a current release verdict or source review. */
import { z } from 'zod'
import { environmentSchema } from './client'

export const archiveMetadataSchema = z.object({
  archived_at: z.string().nullable().optional(),
  archived_by: z.string().nullable().optional(),
  archive_reason: z.string().nullable().optional(),
  reclaimed_at: z.string().nullable().optional(),
  reclaimed_by: z.string().nullable().optional(),
  reclaim_reason: z.string().nullable().optional(),
  reclaim_started_at: z.string().nullable().optional(),
  reclaimed_bytes: z.number().int().nonnegative().optional(),
})
export const candidateEvidenceSchema = z.object({
  stale: z.boolean(),
  eligible_for_release: z.boolean(),
  reason: z.string().nullable(),
  base_hash: z.string().nullable(),
  current_base_hash: z.string().nullable(),
  candidate_hash: z.string().nullable(),
  observed_candidate_hash: z.string().nullable(),
})
export const reconciliationSchema = z.object({
  resolution: z.enum(['verified_success', 'unresolved']),
  reason: z.string().nullable(),
})
export const sourceHandoffSchema = z.object({
  env: environmentSchema,
  kind: z.enum(['csv_candidate', 'transform_revision']),
  source_review_required: z.literal(true),
  automatic_promotion: z.literal(false),
  files: z.array(
    z.object({ path: z.string(), content: z.string(), sha256: z.string() }),
  ),
  candidate_evidence: candidateEvidenceSchema.nullable(),
  release: z.object({
    source_ref: z.string().nullable(),
    target_ref: z.string(),
    checks_url: z.string().nullable(),
    trial_merge_url: z.string().nullable(),
    merge_url: z.string().nullable(),
    requires_signature: z.literal(true),
    blocked_reason: z.string().nullable(),
  }),
})
export type CandidateEvidence = z.infer<typeof candidateEvidenceSchema>
export type SourceHandoff = z.infer<typeof sourceHandoffSchema>
