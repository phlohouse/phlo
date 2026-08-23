/**
 * Server functions reading and writing per-extension settings through phlo-api
 * on behalf of authenticated Observatory users.
 */
import { createServerFn } from '@tanstack/react-start'
import type { Register, ValidateSerializableInput } from '@tanstack/router-core'

import { authMiddleware } from '@/observatory/api/auth'
import { apiGet, apiPut } from '@/server/phlo-api'

export type ExtensionSettingsResponse = {
  settings: Record<string, unknown> | null
  updated_at: string | null
}

type ExtensionSettingsResponseSerializable = ValidateSerializableInput<
  Register,
  ExtensionSettingsResponse
>

export const getExtensionSettings = createServerFn()
  .middleware([authMiddleware])
  .inputValidator((input: { name: string }) => input)
  .handler(async ({ data }): Promise<ExtensionSettingsResponseSerializable> => {
    const response = await apiGet<ExtensionSettingsResponse>(
      `/api/observatory/extensions/${data.name}/settings`,
    )
    return response as ExtensionSettingsResponseSerializable
  })

export const putExtensionSettings = createServerFn()
  .middleware([authMiddleware])
  .inputValidator(
    (input: { name: string; settings: Record<string, unknown> }) => input,
  )
  .handler(async ({ data }): Promise<ExtensionSettingsResponseSerializable> => {
    const response = await apiPut<ExtensionSettingsResponse>(
      `/api/observatory/extensions/${data.name}/settings`,
      { settings: data.settings },
    )
    return response as ExtensionSettingsResponseSerializable
  })
