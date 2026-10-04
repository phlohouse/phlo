/** Typed v1 Jobs, schedules, and runs client used only by Pipelines. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'

import type { ObservatoryResourceResult } from './types'
import { apiGet, apiPost } from '@/server/phlo-api'
import { mutationAuthorization } from '@/server/authenticated-mutation'

const environmentSchema = z.object({ environment: z.enum(['prod', 'staging']) })
const jobSchema = z.object({
  id: z.string().min(1),
  repository_name: z.string().min(1),
  description: z.string().nullable(),
  selected_assets: z.array(z.array(z.string())),
  assets_url: z.string(),
  runs_url: z.string(),
  incidents_url: z.string(),
  resource_id: z.string(),
})
const scheduleSchema = z.object({
  id: z.string().min(1),
  job_id: z.string().min(1),
  status: z.string().min(1),
  resource_id: z.string(),
})
const runSchema = z.object({
  run_id: z.string().min(1),
  job_id: z.string().min(1),
  status: z.string().min(1),
  created_at: z.string(),
  started_at: z.string().nullable(),
  ended_at: z.string().nullable(),
  duration_seconds: z.number().nullable(),
  selected_assets: z.array(z.array(z.string())),
  logs_url: z.string(),
  resource_id: z.string(),
})
const environmentPageSchema = <T extends z.ZodType>(item: T) =>
  z.object({
    env: z.enum(['prod', 'staging']),
    items: z.array(item),
  })
const snapshotSchema = z.object({
  jobs: environmentPageSchema(jobSchema),
  schedules: environmentPageSchema(scheduleSchema),
  runs: environmentPageSchema(runSchema),
})

export type V1PipelineSnapshot = z.infer<typeof snapshotSchema>
export type V1Job = z.infer<typeof jobSchema>
export type V1Schedule = z.infer<typeof scheduleSchema>
export type V1Run = z.infer<typeof runSchema>

const actionResultSchema = z.object({
  env: z.enum(['prod', 'staging']),
  action: z.string(),
  target_id: z.string(),
  status: z.enum(['accepted', 'skipped', 'rejected']),
  result: z.record(z.string(), z.json()),
})

type V1ActionResult = z.infer<typeof actionResultSchema>

function unavailable<T>(error: unknown): ObservatoryResourceResult<T> {
  return {
    data: null,
    error:
      error instanceof Error ? error.message : 'Lakehouse API is unavailable',
  }
}

/** No environment is inferred: callers must submit the selected v1 scope. */
export async function getV1PipelineSnapshotData(
  environment: z.infer<typeof environmentSchema>['environment'],
): Promise<ObservatoryResourceResult<V1PipelineSnapshot>> {
  try {
    const [jobs, schedules, runs] = await Promise.all([
      apiGet('/api/v1/jobs', { env: environment }, 8000),
      apiGet('/api/v1/schedules', { env: environment }, 8000),
      apiGet('/api/v1/runs', { env: environment, limit: 100 }, 8000),
    ])
    const snapshot = snapshotSchema.parse({ jobs, schedules, runs })
    if (
      snapshot.jobs.env !== environment ||
      snapshot.schedules.env !== environment ||
      snapshot.runs.env !== environment
    ) {
      throw new Error(
        'phlo-api returned pipeline data for another environment.',
      )
    }
    return { data: snapshot, error: null }
  } catch (error) {
    return unavailable(error)
  }
}

export const getV1PipelineSnapshot = createServerFn()
  .inputValidator(environmentSchema)
  .handler(
    async ({
      data,
    }): Promise<ObservatoryResourceResult<V1PipelineSnapshot>> => {
      return getV1PipelineSnapshotData(data.environment)
    },
  )

export const launchV1Job = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator(
    environmentSchema.extend({
      id: z.string().min(1),
      idempotencyKey: z.string().min(1),
    }),
  )
  .handler(
    async ({
      data,
      context,
    }): Promise<ObservatoryResourceResult<V1ActionResult>> => {
      try {
        const result = await apiPost(
          `/api/v1/jobs/${encodeURIComponent(data.id)}/launch?env=${data.environment}`,
          {
            idempotency_key: data.idempotencyKey,
            dry_run: false,
            confirmed: true,
          },
          130_000,
          context.authorization,
        )
        return { data: actionResultSchema.parse(result), error: null }
      } catch (error) {
        return unavailable(error)
      }
    },
  )

function scheduleAction(action: 'pause' | 'resume') {
  return createServerFn()
    .middleware([mutationAuthorization])
    .inputValidator(
      environmentSchema.extend({
        id: z.string().min(1),
        idempotencyKey: z.string().min(1),
      }),
    )
    .handler(
      async ({
        data,
        context,
      }): Promise<ObservatoryResourceResult<V1ActionResult>> => {
        try {
          const result = await apiPost(
            `/api/v1/schedules/${encodeURIComponent(data.id)}/${action}?env=${data.environment}`,
            {
              idempotency_key: data.idempotencyKey,
              expected_status: action === 'pause' ? 'RUNNING' : 'STOPPED',
              confirmed: true,
            },
            130_000,
            context.authorization,
          )
          return { data: actionResultSchema.parse(result), error: null }
        } catch (error) {
          return unavailable(error)
        }
      },
    )
}

export const pauseV1Schedule = scheduleAction('pause')
export const resumeV1Schedule = scheduleAction('resume')

export function newV1PipelineIdempotencyKey(): string {
  return `pipeline-${crypto.randomUUID()}`
}
