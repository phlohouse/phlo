/** Validates and executes pipeline listing, scheduling, and run operations. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

export const runStatusSchema = z.enum([
  'NOT_STARTED',
  'MANAGED',
  'QUEUED',
  'STARTING',
  'STARTED',
  'SUCCESS',
  'FAILURE',
  'CANCELING',
  'CANCELED',
])
const jobSchema = z.object({
  id: z.string(),
  repository_name: z.string(),
  description: z.string().nullable(),
  domain: z.string().nullable(),
  owners: z.array(z.string()),
  source: z.string().nullable(),
  feeds_batch_release: z.boolean(),
  selected_assets: z.array(z.array(z.string())),
})
export const runSchema = z.object({
  run_id: z.string(),
  job_id: z.string(),
  status: runStatusSchema,
  created_at: z.string(),
  started_at: z.string().nullable(),
  ended_at: z.string().nullable(),
  duration_seconds: z.number().nonnegative().nullable(),
  selected_assets: z.array(z.array(z.string())),
  tags: z.record(z.string(), z.string()).optional(),
})
export const jobsSchema = z.object({
  env: environmentSchema,
  items: z.array(jobSchema),
  next_cursor: z.string().nullable(),
})
const runsSchema = z.object({
  env: environmentSchema,
  items: z.array(runSchema),
  next_cursor: z.string().nullable(),
})
type RunError = {
  message: string
  class_name: string | null
  stack: Array<string>
  causes: Array<RunError>
}
const runErrorSchema: z.ZodType<RunError> = z.lazy(() =>
  z.object({
    message: z.string(),
    class_name: z.string().nullable(),
    stack: z.array(z.string()),
    causes: z.array(runErrorSchema),
  }),
)
const capturedLogsSchema = z.object({
  file_key: z.string(),
  step_keys: z.array(z.string()),
  stdout: z.string().nullable(),
  stderr: z.string().nullable(),
  available: z.boolean(),
  truncated: z.boolean(),
})
const eventsSchema = z.object({
  env: environmentSchema,
  run_id: z.string(),
  truncated: z.boolean(),
  next_cursor: z.string().nullable(),
  items: z.array(
    z.object({
      event_type: z.string(),
      message: z.string(),
      timestamp: z.string(),
      step_key: z.string().nullable(),
      level: z.string().nullable(),
      error: runErrorSchema.nullable(),
      captured_file_key: z.string().nullable(),
      captured_step_keys: z.array(z.string()),
    }),
  ),
  captured_logs: z.array(capturedLogsSchema).optional(),
  captured_logs_available: z.boolean().optional(),
})
const schedulesSchema = z.object({
  env: environmentSchema,
  items: z.array(
    z.object({
      id: z.string(),
      job_id: z.string(),
      status: z.enum(['RUNNING', 'STOPPED']).or(z.string()),
    }),
  ),
})
const jobRequest = z.object({
  env: environmentSchema,
  id: z.string().min(1),
  run: z.string().min(1).optional(),
})
const runLogRequest = z.object({
  env: environmentSchema,
  id: z.string().min(1),
  run: z.string().min(1),
  cursor: z.string().min(1).optional(),
})

export type ApiRun = z.infer<typeof runSchema>

export const maintenanceSchema = z.object({
  env: environmentSchema,
  status: z.enum(['configured', 'unavailable']),
  items: z.array(
    z.object({
      id: z.string(),
      starts_at: z.string(),
      ends_at: z.string(),
      description: z.string().nullable(),
    }),
  ),
})

export async function collectRunPages(
  read: (cursor: string | null) => Promise<z.infer<typeof runsSchema>>,
) {
  const items: Array<ApiRun> = []
  const seen = new Set<string>()
  let cursor: string | null = null
  do {
    const page = await read(cursor)
    items.push(...page.items)
    cursor = page.next_cursor
    if (cursor && seen.has(cursor))
      throw new Error('Run history cursor did not advance.')
    if (cursor) seen.add(cursor)
  } while (cursor)
  return items
}

function readRuns(env: z.infer<typeof environmentSchema>, jobId?: string) {
  const base = `api/v1/runs?env=${env}&limit=100${jobId ? `&job_id=${encodeURIComponent(jobId)}` : ''}`
  return collectRunPages((cursor) =>
    phloApi(
      `${base}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
      runsSchema,
      { env },
    ),
  )
}

export const getPipelineList = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(async ({ data: env }) => {
    const [jobs, runs, maintenance] = await Promise.all([
      phloApi(`api/v1/jobs?env=${env}`, jobsSchema, { env }),
      readRuns(env),
      phloApi(`api/v1/maintenance-windows?env=${env}`, maintenanceSchema, {
        env,
      }),
    ])
    return {
      jobs: jobs.items,
      runs,
      maintenance,
      env,
      observed_at: new Date().toISOString(),
    }
  })

export const getPipelineJob = createServerFn({ method: 'GET' })
  .inputValidator(jobRequest)
  .handler(async ({ data: { env, id, run } }) => {
    const job = await phloApi(
      `api/v1/jobs/${encodeURIComponent(id)}?env=${env}`,
      jobSchema,
    )
    const [runs, schedules, summary, patterns, jobs, maintenance] =
      await Promise.all([
        readRuns(env, id),
        phloApi(
          `api/v1/jobs/${encodeURIComponent(id)}/schedules?env=${env}`,
          schedulesSchema,
          { env },
        ),
        phloApi(
          `api/v1/jobs/${encodeURIComponent(id)}/summary?env=${env}`,
          z.object({
            env: environmentSchema,
            scanned_runs: z.number(),
            counts_by_status: z.record(z.string(), z.number()),
            duration_histogram_seconds: z.record(z.string(), z.number()),
          }),
          { env },
        ),
        phloApi(
          `api/v1/jobs/${encodeURIComponent(id)}/patterns?env=${env}`,
          z.object({
            env: environmentSchema,
            scanned_runs: z.number(),
            items: z.array(
              z.object({
                kind: z.enum(['failure', 'slow_run']),
                count: z.number(),
                run_ids: z.array(z.string()),
              }),
            ),
          }),
          { env },
        ),
        phloApi(`api/v1/jobs?env=${env}`, jobsSchema, { env }),
        phloApi(`api/v1/maintenance-windows?env=${env}`, maintenanceSchema, {
          env,
        }),
      ])
    const selected = run
      ? await phloApi(
          `api/v1/runs/${encodeURIComponent(run)}?env=${env}`,
          runSchema,
        )
      : (runs[0] ?? null)
    if (selected && selected.job_id !== id)
      throw new Error('The selected run belongs to a different job.')
    let events: z.infer<typeof eventsSchema> | null = null
    if (selected) {
      events = await readRunLogPage({ env, id, run: selected.run_id })
    }
    return {
      job,
      runs,
      schedules: schedules.items,
      summary,
      patterns,
      siblings: job.domain
        ? jobs.items.filter((item) => item.domain === job.domain)
        : [],
      maintenance,
      selected,
      events,
      env,
    }
  })

export const getRunLogPage = createServerFn({ method: 'GET' })
  .inputValidator(runLogRequest)
  .handler(async ({ data }) => readRunLogPage(data))

async function readRunLogPage({
  env,
  id,
  run,
  cursor,
}: z.infer<typeof runLogRequest>) {
  const selected = await phloApi(
    `api/v1/runs/${encodeURIComponent(run)}?env=${env}`,
    runSchema,
    { env },
  )
  if (selected.job_id !== id)
    throw new Error('The selected run belongs to a different job.')
  const page = await phloApi(
    `api/v1/runs/${encodeURIComponent(run)}/logs?env=${env}&limit=100${cursor ? `&after_cursor=${encodeURIComponent(cursor)}` : ''}`,
    eventsSchema,
    { env },
  )
  if (page.env !== env || page.run_id !== run)
    throw new Error('Run log page identity did not match the requested run.')
  if (page.truncated && !page.next_cursor)
    throw new Error('Run event cursor is unavailable.')
  if (cursor && page.next_cursor === cursor)
    throw new Error('Run event cursor did not advance.')
  return page
}

export const getRunTimeline = getPipelineList

const operationRequest = z.object({
  env: environmentSchema,
  idempotency_key: z.string().uuid(),
  confirmed: z.literal(true),
})
const actionSchema = z.object({
  env: environmentSchema,
  status: z.enum(['accepted', 'skipped', 'rejected']),
})

export const launchJob = createServerFn({ method: 'POST' })
  .inputValidator(
    operationRequest.extend({
      job_id: z.string().min(1),
      partition_key: z.string().min(1).max(256).optional(),
    }),
  )
  .handler(
    async ({ data: { env, job_id, idempotency_key, partition_key } }) => {
      const response = await phloApi(
        `api/v1/jobs/${encodeURIComponent(job_id)}/launch?env=${env}`,
        actionSchema.extend({
          result: z.object({
            run_id: z.string(),
            status: z.literal('accepted'),
          }),
        }),
        {
          env,
          body: {
            idempotency_key,
            partition_key,
            dry_run: false,
            confirmed: true,
          },
        },
      )
      if (response.status !== 'accepted')
        throw new Error('Dagster did not accept the job launch.')
      return { run_id: response.result.run_id }
    },
  )

export const changeSchedule = createServerFn({ method: 'POST' })
  .inputValidator(
    operationRequest.extend({
      schedule_id: z.string().min(1),
      action: z.enum(['pause', 'resume']),
      expected_status: z.enum(['RUNNING', 'STOPPED']),
    }),
  )
  .handler(
    async ({
      data: { env, schedule_id, action, expected_status, idempotency_key },
    }) => {
      const response = await phloApi(
        `api/v1/schedules/${encodeURIComponent(schedule_id)}/${action}?env=${env}`,
        actionSchema.extend({
          result: z.object({
            status: z.literal('accepted'),
            schedule_status: z.enum(['RUNNING', 'STOPPED']),
          }),
        }),
        { env, body: { idempotency_key, expected_status, confirmed: true } },
      )
      if (response.status !== 'accepted')
        throw new Error(`Dagster did not accept the schedule ${action}.`)
      return { status: response.result.schedule_status }
    },
  )

export const cancelRun = createServerFn({ method: 'POST' })
  .inputValidator(operationRequest.extend({ run_id: z.string().min(1) }))
  .handler(async ({ data: { env, run_id, idempotency_key } }) => {
    const response = await phloApi(
      `api/v1/runs/${encodeURIComponent(run_id)}/cancel?env=${env}`,
      actionSchema.extend({
        result: z.object({ accepted: z.boolean() }).passthrough(),
      }),
      {
        env,
        body: {
          idempotency_key,
          expected_status: 'STARTED',
          dry_run: false,
          confirmed: true,
          reason: 'Canceled by an operator in Phlo Observatory.',
        },
      },
    )
    if (response.status !== 'accepted' || !response.result.accepted)
      throw new Error('Dagster did not accept the cancellation.')
    return { accepted: true }
  })

export const retryRun = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      run_id: z.string().min(1),
      idempotency_key: z.string().uuid(),
      confirmed: z.literal(true),
    }),
  )
  .handler(async ({ data: { env, run_id, idempotency_key } }) => {
    const response = await phloApi(
      `api/v1/runs/${encodeURIComponent(run_id)}/retry?env=${env}`,
      z.object({
        env: environmentSchema,
        status: z.enum(['accepted', 'skipped', 'rejected']),
        result: z.object({
          accepted: z.boolean(),
          run_id: z.string().nullable(),
        }),
      }),
      {
        env,
        body: {
          idempotency_key,
          expected_status: 'FAILURE',
          dry_run: false,
          confirmed: true,
        },
      },
    )
    if (
      response.status !== 'accepted' ||
      !response.result.accepted ||
      !response.result.run_id
    )
      throw new Error('Dagster did not accept the retry.')
    return { run_id: response.result.run_id }
  })
