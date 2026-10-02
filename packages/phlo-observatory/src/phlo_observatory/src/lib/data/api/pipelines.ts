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
    }),
  ),
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

export type ApiRun = z.infer<typeof runSchema>

export const getPipelineList = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(async ({ data: env }) => {
    const [jobs, runs] = await Promise.all([
      phloApi(`api/v1/jobs?env=${env}`, jobsSchema, { env }),
      phloApi(`api/v1/runs?env=${env}&limit=100`, runsSchema, { env }),
    ])
    return {
      jobs: jobs.items,
      runs: runs.items,
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
    const [runs, schedules] = await Promise.all([
      phloApi(
        `api/v1/runs?env=${env}&job_id=${encodeURIComponent(id)}&limit=100`,
        runsSchema,
        { env },
      ),
      phloApi(
        `api/v1/jobs/${encodeURIComponent(id)}/schedules?env=${env}`,
        schedulesSchema,
        { env },
      ),
    ])
    const selected = run
      ? await phloApi(
          `api/v1/runs/${encodeURIComponent(run)}?env=${env}`,
          runSchema,
        )
      : (runs.items[0] ?? null)
    if (selected && selected.job_id !== id)
      throw new Error('The selected run belongs to a different job.')
    const events = selected
      ? await phloApi(
          `api/v1/runs/${encodeURIComponent(selected.run_id)}/timeline?env=${env}&limit=100`,
          eventsSchema,
          { env },
        )
      : null
    return {
      job,
      runs: runs.items,
      schedules: schedules.items,
      selected,
      events,
      env,
    }
  })

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
