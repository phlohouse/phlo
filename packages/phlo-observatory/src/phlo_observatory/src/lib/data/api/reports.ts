import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { phloApi } from './client'

const evidenceRows = z.array(z.record(z.string(), z.json()))
const reportSchema = z.object({
  schema_version: z.number().int(),
  project_id: z.string(),
  run_id: z.string(),
  attempt: z.number().int().positive(),
  lifecycle: z.object({
    run: z
      .object({
        status: z.string(),
        pipeline_name: z.string().nullable(),
        provider_run_id: z.string().nullable(),
        started_at: z.string().nullable(),
        finished_at: z.string().nullable(),
        evidence_completeness: z.string(),
      })
      .catchall(z.json())
      .nullable(),
    events: evidenceRows,
  }),
  stages: evidenceRows,
  inputs: evidenceRows,
  staging: evidenceRows,
  outputs: evidenceRows,
  lineage: evidenceRows,
  transformations: evidenceRows,
  quality: evidenceRows,
  iceberg_snapshots: evidenceRows,
  catalog_changes: evidenceRows,
  artifacts: evidenceRows,
  terminal_outcome: z
    .object({
      status: z.string(),
      source: z.string(),
      evidence_id: z.string(),
      observed_at: z.string().nullable(),
    })
    .nullable(),
  gaps: z.array(
    z.object({ field: z.string(), status: z.string(), reason: z.string() }),
  ),
})

export const getRunReport = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({
      project: z.string().min(1),
      run: z.string().min(1),
      attempt: z.number().int().positive(),
    }),
  )
  .handler(async ({ data }) => {
    const report = await phloApi(
      `/api/v1/projects/${encodeURIComponent(data.project)}/runs/${encodeURIComponent(data.run)}/attempts/${data.attempt}/report`,
      reportSchema,
    )
    if (
      report.project_id !== data.project ||
      report.run_id !== data.run ||
      report.attempt !== data.attempt
    )
      throw new Error('Phlo returned evidence for a different run attempt.')
    return report
  })
