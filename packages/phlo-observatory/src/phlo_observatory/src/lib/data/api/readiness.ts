/** Validates authenticated capability observations, not container liveness. */
import { z } from 'zod'
import { createServerFn } from '@tanstack/react-start'
import { environmentSchema, phloApi } from './client'

export const readinessSchema = z.object({
  env: environmentSchema,
  observed_at: z.string(),
  correlation_id: z.string().regex(/^[A-Za-z0-9_-]{1,64}$/),
  capabilities: z.record(
    z.string(),
    z
      .array(
        z.object({
          state: z.enum([
            'configured',
            'ready',
            'unavailable',
            'degraded',
            'disabled',
          ]),
          reason_code: z.string().regex(/^[a-z][a-z0-9_]{0,63}$/),
          message: z.string().max(500),
          remediation: z.string().max(1000),
        }),
      )
      .min(1),
  ),
})

export const getReadiness = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(({ data: env }) =>
    phloApi(`/api/v1/readiness?env=${env}`, readinessSchema, {
      env,
      timeoutMs: 15_000,
    }),
  )
