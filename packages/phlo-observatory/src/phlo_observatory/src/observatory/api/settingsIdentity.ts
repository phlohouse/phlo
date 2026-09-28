/** Settings-screen client for the canonical authenticated caller contract. */
import { createMiddleware, createServerFn } from '@tanstack/react-start'
import { z } from 'zod'

import type { ObservatoryResourceResult } from './types'
import { mutationBearerAuthorization } from '@/server/authenticated-mutation'
import { apiGet } from '@/server/phlo-api'

const currentIdentitySchema = z.object({
  subject: z.string(),
  principal_type: z.string(),
  email: z.string().nullable(),
  roles: z.array(z.string()),
  permissions: z.record(z.string(), z.array(z.string())),
})

export type CurrentIdentity = z.infer<typeof currentIdentitySchema>

const requestAuthorization = createMiddleware({ type: 'request' }).server(
  ({ next, request }) =>
    next({
      context: {
        authorization: mutationBearerAuthorization(
          request.headers.get('authorization'),
        ),
      },
    }),
)

export const getCurrentIdentity = createServerFn()
  .middleware([requestAuthorization])
  .handler(
    async ({
      context,
    }): Promise<ObservatoryResourceResult<CurrentIdentity>> => {
      try {
        return {
          data: currentIdentitySchema.parse(
            await apiGet(
              '/api/v1/me',
              undefined,
              30_000,
              context.authorization,
            ),
          ),
          error: null,
        }
      } catch (error) {
        return {
          data: null,
          error:
            error instanceof Error ? error.message : 'phlo-api is unavailable',
        }
      }
    },
  )
