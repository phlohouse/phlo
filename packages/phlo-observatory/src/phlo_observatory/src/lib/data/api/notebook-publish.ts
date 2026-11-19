/** Notebook schedules, background runs, git commits, promotion to dbt models and lineage. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'
import { digestSchema, notebookSchema, outcome, pathInput } from './notebooks'

const runSchema = z.object({
  id: z.string(),
  trigger: z.enum(['manual', 'schedule']),
  actor: z.string(),
  env: environmentSchema,
  status: z.enum(['running', 'ok', 'failed']),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  message: z.string().nullable(),
  cells: z.array(
    z.object({
      cell_id: z.string(),
      status: z.string(),
      duration_ms: z.number().int(),
      error: z.string().nullable(),
    }),
  ),
})
const runsSchema = z.object({
  items: z.array(runSchema),
  schedules_enabled: z.boolean(),
})
const gitSchema = z.object({
  enabled: z.boolean(),
  branch: z.string().nullable(),
  protected: z.boolean(),
  changed: z.array(z.string()),
  message: z.string().nullable(),
})
const promoteSchema = z.object({
  files: z.array(
    z.object({ path: z.string(), content: z.string(), exists: z.boolean() }),
  ),
  applied: z.boolean(),
})
const assetSchema = z.object({
  id: z.string(),
  key: z.array(z.string()),
  relation: z.string().nullable(),
  compute_kind: z.string().nullable(),
})
const assetPageSchema = z.object({
  env: environmentSchema,
  items: z.array(assetSchema),
  next_cursor: z.string().nullable(),
})

export type NotebookRun = z.infer<typeof runSchema>
export type NotebookRuns = z.infer<typeof runsSchema>
export type GitState = z.infer<typeof gitSchema>
export type PromotePlan = z.infer<typeof promoteSchema>
export type LineageAsset = z.infer<typeof assetSchema>

/** Sets or clears the cron schedule. Scheduled runs act as the person who set it. */
export const setNotebookSchedule = createServerFn({ method: 'POST' })
  .inputValidator(
    pathInput.extend({
      cron: z.string().max(120).nullable(),
      run_env: environmentSchema,
      enabled: z.boolean(),
      expected_digest: digestSchema,
    }),
  )
  .handler(({ data }) =>
    outcome(
      () =>
        phloApi(
          `/api/v1/notebooks/schedule?env=${data.env}&path=${encodeURIComponent(data.path)}`,
          notebookSchema,
          {
            env: data.env,
            method: 'PUT',
            body: {
              cron: data.cron,
              env: data.run_env,
              enabled: data.enabled,
              expected_digest: data.expected_digest,
            },
          },
        ),
      'The schedule was not saved.',
    ),
  )

export const getNotebookRuns = createServerFn({ method: 'GET' })
  .inputValidator(pathInput)
  .handler(({ data }) =>
    phloApi(
      `/api/v1/notebooks/runs?env=${data.env}&path=${encodeURIComponent(data.path)}`,
      runsSchema,
      { env: data.env },
    ),
  )

/** Runs the saved notebook top to bottom in its own kernel; it keeps going if the page closes. */
export const startNotebookRun = createServerFn({ method: 'POST' })
  .inputValidator(pathInput)
  .handler(({ data }) =>
    outcome(
      () =>
        phloApi(
          `/api/v1/notebooks/runs?env=${data.env}&path=${encodeURIComponent(data.path)}`,
          runSchema,
          { env: data.env, method: 'POST' },
        ),
      'The run did not start.',
    ),
  )

export const getNotebookGit = createServerFn({ method: 'GET' })
  .inputValidator(z.object({ env: environmentSchema }))
  .handler(({ data }) =>
    phloApi(`/api/v1/notebooks/git?env=${data.env}`, gitSchema, {
      env: data.env,
    }),
  )

export const commitNotebookFiles = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      paths: z.array(z.string().min(1).max(600)).min(1).max(50),
      message: z.string().min(1).max(2000),
    }),
  )
  .handler(({ data }) =>
    outcome(
      () =>
        phloApi(
          `/api/v1/notebooks/git/commit?env=${data.env}`,
          z.object({ branch: z.string(), commit: z.string() }),
          {
            env: data.env,
            method: 'POST',
            body: { paths: data.paths, message: data.message },
            timeoutMs: 60_000,
          },
        ),
      'The commit was refused.',
    ),
  )

/** Previews, or with `apply` writes, a dbt model made from a Trino SQL cell. */
export const promoteNotebookCell = createServerFn({ method: 'POST' })
  .inputValidator(
    pathInput.extend({
      cell_id: z.string().min(1).max(64),
      model: z.string().regex(/^[a-z_][a-z0-9_]{0,63}$/),
      apply: z.boolean(),
    }),
  )
  .handler(({ data }) =>
    outcome(
      () =>
        phloApi(
          `/api/v1/notebooks/promote?env=${data.env}&path=${encodeURIComponent(data.path)}`,
          promoteSchema,
          {
            env: data.env,
            method: 'POST',
            body: {
              cell_id: data.cell_id,
              model: data.model,
              apply: data.apply,
            },
          },
        ),
      'This cell cannot become a model.',
    ),
  )

function relationKey(name: string) {
  return name.toLowerCase().replace(/"/g, '').split('.').slice(-2).join('.')
}

/** Pipeline assets that produce the tables a notebook reads, matched by their table relation. */
export const getNotebookLineage = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      tables: z.array(z.string().min(1).max(400)).max(100),
    }),
  )
  .handler(async ({ data }) => {
    const wanted = new Set(data.tables.map(relationKey))
    const found: Array<LineageAsset> = []
    let cursor: string | null = null
    // Five pages covers 500 assets; a larger project says so instead of guessing.
    for (let page = 0; page < 5; page++) {
      const query = new URLSearchParams({ env: data.env, limit: '100' })
      if (cursor) query.set('cursor', cursor)
      const result: z.infer<typeof assetPageSchema> = await phloApi(
        `/api/v1/assets?${query}`,
        assetPageSchema,
        { env: data.env },
      )
      for (const asset of result.items)
        if (asset.relation && wanted.has(relationKey(asset.relation)))
          found.push(asset)
      cursor = result.next_cursor
      if (!cursor) break
    }
    return { items: found, complete: cursor === null }
  })
