/** Environment-scoped source inventory and project-scoped workflow authoring. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, optionalRead, phloApi } from './client'
import { jobsSchema } from './pipelines'

const sourceSchema = z.object({
  id: z.string(),
  key: z.array(z.string()),
  description: z.string().nullable(),
  compute_kind: z.string().nullable(),
  owner: z.string().nullable().optional(),
  source_name: z.string().nullable().optional(),
  relation: z.string().nullable(),
  last_materialization_at: z.string().nullable(),
  last_run_id: z.string().nullable(),
  freshness_status: z.enum(['fresh', 'stale', 'unknown']).optional(),
})
const sourcePageSchema = z.object({
  env: environmentSchema,
  items: z.array(sourceSchema),
  next_cursor: z.string().nullable(),
})
export type Source = z.infer<typeof sourceSchema>
export const sourceWorkflowInputSchema = z
  .object({
    domain: z.string().regex(/^[a-z][a-z0-9_]{0,62}$/),
    table: z.string().regex(/^[a-z][a-z0-9_]{0,62}$/),
    unique_key: z.string().regex(/^[a-z][a-z0-9_]{0,62}$/),
    api_base_url: z.url().refine((value) => {
      const url = new URL(value)
      return (
        ['http:', 'https:'].includes(url.protocol) &&
        !url.username &&
        !url.password &&
        !url.search &&
        !url.hash
      )
    }, 'Use an HTTP URL without credentials, query parameters or fragments.'),
    fields: z
      .array(
        z
          .string()
          .regex(/^[a-z][a-z0-9_]*:(str|int|float|bool|datetime|date)\??$/),
      )
      .min(1)
      .max(100),
  })
  .strict()
export type SourceWorkflowInput = z.infer<typeof sourceWorkflowInputSchema>
const workflowResultSchema = z.object({
  workflow_type: z.string(),
  provider: z.string(),
  domain: z.string(),
  table: z.string(),
  files: z.array(z.string()),
  next_steps: z.array(z.string()),
})
const validationSchema = z.object({
  valid: z.boolean(),
  errors: z.array(z.string()),
  workflow_path: z.string().nullable().optional(),
  schema_path: z.string().nullable().optional(),
})

export const getSources = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({ env: environmentSchema, cursor: z.string().optional() }),
  )
  .handler(async ({ data }) => {
    const params = new URLSearchParams({ env: data.env, limit: '100' })
    if (data.cursor) params.set('cursor', data.cursor)
    const [sources, jobs] = await Promise.all([
      phloApi(`api/v1/sources?${params}`, sourcePageSchema, { env: data.env }),
      optionalRead(
        phloApi(`api/v1/jobs?env=${encodeURIComponent(data.env)}`, jobsSchema, {
          env: data.env,
        }),
      ),
    ])
    return { ...sources, jobs }
  })

export const createSourceWorkflow = createServerFn({ method: 'POST' })
  .inputValidator(sourceWorkflowInputSchema)
  .handler(async ({ data }) => {
    const result = await phloApi(
      'api/v1/authoring/workflows',
      workflowResultSchema,
      {
        method: 'POST',
        body: {
          ...data,
          workflow_type: 'ingestion',
          provider: 'dlt',
          contribution_id: 'dlt.rest-api-source',
        },
      },
    )
    const validations = await Promise.all(
      result.files
        .filter((file) => file.endsWith('.py') && file.startsWith('workflows/'))
        .map(async (file) => ({
          file,
          ...(await phloApi(
            file.startsWith('workflows/schemas/')
              ? 'api/v1/authoring/schemas/validate'
              : 'api/v1/authoring/workflows/validate',
            validationSchema,
            { method: 'POST', body: { path: file } },
          ).catch(() => ({
            valid: false,
            errors: [
              'Files were created, but validation could not be completed. Inspect the files before continuing.',
            ],
          }))),
        })),
    )
    return { ...result, validations }
  })

export function sourceJobIds(
  source: Source,
  jobs: z.infer<typeof jobsSchema>['items'],
) {
  return jobs
    .filter((job) =>
      job.selected_assets.some(
        (key) =>
          key.length === source.key.length &&
          key.every((part, index) => part === source.key[index]),
      ),
    )
    .map((job) => job.id)
}
