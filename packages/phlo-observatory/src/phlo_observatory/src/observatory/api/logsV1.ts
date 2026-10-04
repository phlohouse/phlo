/** Run-scoped log evidence from the canonical v1 jobs API. */
import { createMiddleware, createServerFn } from '@tanstack/react-start'
import { z } from 'zod'

import type {
  ObservatoryLogEvent,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import type { ObservatoryEnvironment } from '@/observatory/api/environment'
import { selectedEnvironment, v1Endpoint } from '@/observatory/api/environment'
import { mutationBearerAuthorization } from '@/server/authenticated-mutation'
import { apiGet } from '@/server/phlo-api'

const runsSchema = z.object({
  env: z.enum(['prod', 'staging']),
  items: z.array(z.object({ run_id: z.string().min(1) })),
})
const runLogsSchema = z.object({
  env: z.enum(['prod', 'staging']),
  run_id: z.string().min(1),
  items: z.array(
    z.object({
      event_type: z.string().min(1),
      message: z.string(),
      timestamp: z.string(),
      step_key: z.string().nullable(),
    }),
  ),
})

const MAX_RUNS = 20

export async function getV1RunLogRecordsData(
  environment: ObservatoryEnvironment,
  authorization?: string,
): Promise<ObservatoryResourceResult<Array<ObservatoryLogEvent>>> {
  try {
    const runs = runsSchema.parse(
      await apiGet<unknown>(
        v1Endpoint('/api/v1/runs?limit=100', environment),
        undefined,
        8000,
        authorization,
      ),
    )
    if (runs.env !== environment) {
      throw new Error('phlo-api returned runs for another environment.')
    }

    const selectedRuns = runs.items.slice(0, MAX_RUNS)
    const logPages = await Promise.all(
      selectedRuns.map(async ({ run_id }) => {
        const logs = runLogsSchema.parse(
          await apiGet<unknown>(
            v1Endpoint(
              `/api/v1/runs/${encodeURIComponent(run_id)}/logs?limit=50`,
              environment,
            ),
            undefined,
            8000,
            authorization,
          ),
        )
        if (logs.env !== environment || logs.run_id !== run_id) {
          throw new Error(
            'phlo-api returned logs for another run or environment.',
          )
        }
        return logs.items.map(
          (event, index): ObservatoryLogEvent => ({
            id: `${run_id}:${event.timestamp}:${index}`,
            timestamp: event.timestamp,
            level: eventLevel(event.event_type),
            message: event.message,
            source: 'run',
            resource: { kind: 'run', id: run_id, label: run_id },
            metadata: {
              run_id: run_id,
              event_type: event.event_type,
              ...(event.step_key === null ? {} : { step_key: event.step_key }),
            },
          }),
        )
      }),
    )

    return {
      data: logPages
        .flat()
        .sort(
          (left, right) =>
            Date.parse(right.timestamp ?? '') -
            Date.parse(left.timestamp ?? ''),
        ),
      error: null,
    }
  } catch (error) {
    return {
      data: null,
      error:
        error instanceof Error ? error.message : 'Run logs are unavailable.',
    }
  }
}

function eventLevel(eventType: string): string {
  const normalized = eventType.toLowerCase()
  if (normalized.includes('failure')) return 'error'
  if (normalized.includes('warning')) return 'warning'
  if (normalized.includes('success')) return 'info'
  return 'event'
}

const readAuthorization = createMiddleware({ type: 'request' }).server(
  ({ next, request }) => {
    return next({
      context: {
        authorization: mutationBearerAuthorization(
          request.headers.get('authorization'),
        ),
      },
    })
  },
)

export const getV1RunLogRecords = createServerFn()
  .middleware([readAuthorization])
  .inputValidator(z.object({ environment: z.enum(['prod', 'staging']) }))
  .handler(({ context, data }) =>
    getV1RunLogRecordsData(data.environment, context.authorization),
  )

export async function getSelectedV1RunLogRecords(): Promise<
  ObservatoryResourceResult<Array<ObservatoryLogEvent>>
> {
  const environment = selectedEnvironment()
  if (!environment) {
    return {
      data: null,
      error: 'Select prod or staging before loading run logs.',
    }
  }
  return getV1RunLogRecords({ data: { environment } })
}
