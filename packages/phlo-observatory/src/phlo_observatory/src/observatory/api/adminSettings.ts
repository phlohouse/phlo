/** Client for the canonical durable /api/v1/admin/settings contract. */
import { createMiddleware, createServerFn } from '@tanstack/react-start'
import { z } from 'zod'

import type { ObservatoryResourceResult } from './types'
import {
  mutationAuthorization,
  mutationBearerAuthorization,
} from '@/server/authenticated-mutation'
import { apiGet, apiPut } from '@/server/phlo-api'

const adminSettingValueSchema = z.union([
  z.string(),
  z.number(),
  z.boolean(),
  z.null(),
])
const adminSettingsSchema = z.object({
  version: z.number().int().nonnegative(),
  values: z.record(z.string(), adminSettingValueSchema),
})
const updateSchema = z.object({
  expectedVersion: z.number().int().nonnegative(),
  values: z.record(z.string(), adminSettingValueSchema),
})

export type AdminSettingValue = z.infer<typeof adminSettingValueSchema>
export type AdminSettings = z.infer<typeof adminSettingsSchema>

const readAuthorization = createMiddleware({ type: 'request' }).server(
  ({ next, request }) =>
    next({
      context: {
        authorization: mutationBearerAuthorization(
          request.headers.get('authorization'),
        ),
      },
    }),
)

function unavailable<T>(error: unknown): ObservatoryResourceResult<T> {
  return {
    data: null,
    error: error instanceof Error ? error.message : 'phlo-api is unavailable',
  }
}

export const getAdminSettings = createServerFn()
  .middleware([readAuthorization])
  .handler(
    async ({ context }): Promise<ObservatoryResourceResult<AdminSettings>> => {
      try {
        return {
          data: adminSettingsSchema.parse(
            await apiGet(
              '/api/v1/admin/settings',
              undefined,
              30_000,
              context.authorization,
            ),
          ),
          error: null,
        }
      } catch (error) {
        return unavailable<AdminSettings>(error)
      }
    },
  )

export const putAdminSettings = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator((input) => updateSchema.parse(input))
  .handler(
    async ({
      context,
      data,
    }): Promise<ObservatoryResourceResult<AdminSettings>> => {
      try {
        return {
          data: adminSettingsSchema.parse(
            await apiPut(
              '/api/v1/admin/settings',
              {
                expected_version: data.expectedVersion,
                values: data.values,
              },
              30_000,
              context.authorization,
            ),
          ),
          error: null,
        }
      } catch (error) {
        return unavailable<AdminSettings>(error)
      }
    },
  )
