/** Configures authenticated server-side requests to the Phlo API. */
import { createServerOnlyFn } from '@tanstack/react-start'
import { getRequestHeader } from '@tanstack/react-start/server'
import { z } from 'zod'

export const environmentSchema = z.enum(['prod', 'staging'])
export const environmentSearchSchema = z.object({
  env: environmentSchema.default('prod'),
})

export const overviewSchema = z.object({
  env: environmentSchema,
  asset_count: z.number().int().nonnegative(),
  materialized_asset_count: z.number().int().nonnegative(),
  latest_materialization_at: z.string().nullable(),
  incident_counts: z.record(z.string(), z.number().int().nonnegative()),
  freshness_counts: z.object({
    fresh: z.number().int().nonnegative(),
    stale: z.number().int().nonnegative(),
    unknown: z.number().int().nonnegative(),
  }),
  run_status_counts: z.record(z.string(), z.number().int().nonnegative()),
  run_history_truncated: z.boolean(),
  quality_checks: z.object({
    status: z.enum(['available', 'unknown']),
    counts: z
      .object({
        passing: z.number().int().nonnegative(),
        total: z.number().int().nonnegative(),
        unevaluated: z.number().int().nonnegative(),
      })
      .nullable(),
    failing_assets: z.array(z.string()).nullable().optional(),
    reason: z.string().nullable(),
  }),
})

export const servicesSchema = z.object({
  env: environmentSchema,
  items: z.array(
    z.object({
      id: z.string(),
      status: z.enum([
        'healthy',
        'degraded',
        'unhealthy',
        'unknown',
        'unavailable',
      ]),
      observed_at: z.string().nullable(),
      response_time_seconds: z.number().nullable(),
    }),
  ),
  next_cursor: z.string().nullable(),
})

export type ObservatoryOverview = z.infer<typeof overviewSchema>
export type ObservatoryServiceList = z.infer<typeof servicesSchema>

const apiErrorMessages: Partial<Record<number, string>> = {
  401: 'Sign in to access Phlo. Your session may have expired.',
  403: 'Your account does not have permission for this page or action. Contact a Phlo administrator.',
}

export async function parseApiResponse<T>({
  response,
  schema,
  env,
  responseType,
}: {
  response: Response
  schema: z.ZodType<T>
  env?: z.infer<typeof environmentSchema>
  responseType?: 'json' | 'text'
}): Promise<T> {
  if (!response.ok)
    throw new Error(
      apiErrorMessages[response.status] ??
        `Phlo API request failed (${response.status}).`,
    )
  let payload: unknown
  try {
    const text = await response.text()
    payload =
      responseType === 'text'
        ? text
        : JSON.parse(
            text,
            (_key: string, value: unknown, context?: { source: string }) => {
              // Iceberg uses 64-bit snapshot IDs. Keep their exact JSON digits instead of rounding them.
              if (
                typeof value === 'number' &&
                Number.isInteger(value) &&
                !Number.isSafeInteger(value)
              ) {
                if (context?.source && /^-?\d+$/.test(context.source))
                  return context.source
                throw new Error('An integer exceeds JavaScript precision.')
              }
              return value
            },
          )
  } catch {
    throw new Error('Phlo API returned invalid JSON.')
  }
  let result: T
  try {
    result = schema.parse(payload)
  } catch {
    throw new Error('Phlo API response did not match the expected contract.')
  }
  if (
    env &&
    typeof result === 'object' &&
    result !== null &&
    'env' in result &&
    result.env !== env
  ) {
    throw new Error('Phlo API returned data for a different environment.')
  }
  return result
}

export const phloApi = createServerOnlyFn(
  async <T>(
    path: string,
    schema: z.ZodType<T>,
    options: {
      env?: z.infer<typeof environmentSchema>
      body?: unknown
      method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
      idempotencyKey?: string
      responseType?: 'json' | 'text'
      headers?: Record<string, string>
      timeoutMs?: number
    } = {},
  ): Promise<T> => {
    const baseUrl = process.env.PHLO_API_URL
    if (!baseUrl)
      throw new Error(
        'Phlo API is not configured (set PHLO_API_URL on the server).',
      )

    const headers = new Headers(options.headers)
    const authorization = getRequestHeader('authorization')
    const accessToken = getRequestHeader('x-auth-request-access-token')
    const bearer = authorization?.startsWith('Bearer ')
      ? authorization
      : accessToken
        ? `Bearer ${accessToken}`
        : undefined
    if (bearer) headers.set('authorization', bearer)
    if (options.body !== undefined)
      headers.set('content-type', 'application/json')
    if (options.idempotencyKey)
      headers.set('idempotency-key', options.idempotencyKey)

    let response: Response
    try {
      response = await fetch(new URL(path, `${baseUrl.replace(/\/$/, '')}/`), {
        headers,
        method: options.method ?? (options.body === undefined ? 'GET' : 'POST'),
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
        cache: 'no-store',
        signal: AbortSignal.timeout(options.timeoutMs ?? 10_000),
      })
    } catch {
      throw new Error('Phlo API is unreachable.')
    }

    return parseApiResponse({
      response,
      schema,
      env: options.env,
      responseType: options.responseType,
    })
  },
)
