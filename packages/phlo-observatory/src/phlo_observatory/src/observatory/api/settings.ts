/**
 * Server functions reading and persisting Observatory settings; payloads are
 * validated against the shared settings schema before hitting phlo-api.
 */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import type { Register, ValidateSerializableInput } from '@tanstack/router-core'

import type { ObservatorySettings } from '@/lib/observatorySettings'
import {
  getFallbackObservatorySettings,
  observatorySettingsSchema,
} from '@/lib/observatorySettings'
import { authMiddleware } from '@/observatory/api/auth'
import { apiGet, apiPut } from '@/server/phlo-api'

export type ObservatorySettingsResponse = {
  settings: Record<string, unknown> | null
  updated_at: string | null
}

type ObservatorySettingsResponseSerializable = ValidateSerializableInput<
  Register,
  ObservatorySettingsResponse
>

const observatorySettingsInputSchema = z.object({
  settings: observatorySettingsSchema,
})

type ObservatorySettingsInput = z.infer<typeof observatorySettingsInputSchema>

export const getObservatorySettingsDefaults = createServerFn().handler(
  (): ObservatorySettings => {
    const fallback = getFallbackObservatorySettings()

    return {
      ...fallback,
      connections: {
        dagsterGraphqlUrl:
          process.env.DAGSTER_GRAPHQL_URL ||
          fallback.connections.dagsterGraphqlUrl,
        trinoUrl: process.env.TRINO_URL || fallback.connections.trinoUrl,
        nessieUrl: process.env.NESSIE_URL || fallback.connections.nessieUrl,
      },
    }
  },
)

export const getObservatorySettings = createServerFn()
  .middleware([authMiddleware])
  .handler(async (): Promise<ObservatorySettingsResponseSerializable> => {
    const response = await apiGet<ObservatorySettingsResponse>(
      '/api/observatory/preferences',
    )
    return response as ObservatorySettingsResponseSerializable
  })

export const putObservatorySettings = createServerFn()
  .middleware([authMiddleware])
  .inputValidator((input: ObservatorySettingsInput) =>
    observatorySettingsInputSchema.parse(input),
  )
  .handler(
    async ({ data }): Promise<ObservatorySettingsResponseSerializable> => {
      const response = await apiPut<ObservatorySettingsResponse>(
        '/api/observatory/preferences',
        data,
      )
      return response as ObservatorySettingsResponseSerializable
    },
  )
