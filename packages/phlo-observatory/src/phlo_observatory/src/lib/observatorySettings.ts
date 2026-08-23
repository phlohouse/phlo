/**
 * Zod schema, defaults, and storage helpers for Observatory user settings.
 * Shared by the client preferences UI and the server settings API so both
 * validate against one definition.
 */
import { z } from 'zod'

const OBSERVATORY_SETTINGS_STORAGE_KEY = 'phlo-observatory-settings-v1'

const densitySchema = z.enum(['comfortable', 'compact'])
const dateFormatSchema = z.enum(['iso', 'local'])

export const observatorySettingsSchema = z.object({
  version: z.literal(1),
  connections: z.object({
    dagsterGraphqlUrl: z.string().min(1),
    trinoUrl: z.string().min(1),
    nessieUrl: z.string().min(1),
  }),
  defaults: z.object({
    branch: z.string().min(1),
    catalog: z.string().min(1),
    schema: z.string().min(1),
  }),
  query: z.object({
    readOnlyMode: z.boolean(),
    defaultLimit: z.number().int().min(1).max(100_000),
    maxLimit: z.number().int().min(1).max(100_000),
    timeoutMs: z.number().int().min(1_000).max(300_000),
  }),
  ui: z.object({
    density: densitySchema,
    dateFormat: dateFormatSchema,
  }),
  // Auth settings (phlo-h2c)
  auth: z
    .object({
      token: z.string().optional(),
    })
    .optional(),
  // Real-time polling settings (phlo-cil)
  realtime: z
    .object({
      enabled: z.boolean(),
      intervalMs: z.number().int().min(1000).max(60000),
    })
    .optional(),
})

export type ObservatorySettings = z.infer<typeof observatorySettingsSchema>
export function getFallbackObservatorySettings(): ObservatorySettings {
  return {
    version: 1,
    connections: {
      dagsterGraphqlUrl: 'http://localhost:3000/graphql',
      trinoUrl: 'http://localhost:8080',
      nessieUrl: 'http://localhost:19120/api/v2',
    },
    defaults: {
      branch: 'main',
      catalog: 'iceberg',
      schema: 'gold',
    },
    query: {
      readOnlyMode: true,
      defaultLimit: 100,
      maxLimit: 5000,
      timeoutMs: 30_000,
    },
    ui: {
      density: 'comfortable',
      dateFormat: 'iso',
    },
    auth: {
      token: undefined,
    },
    realtime: {
      enabled: true,
      intervalMs: 5000,
    },
  }
}

export function parseObservatorySettings(
  input: unknown,
  fallback: ObservatorySettings = getFallbackObservatorySettings(),
): ObservatorySettings {
  const parsed = observatorySettingsSchema.safeParse(input)
  if (!parsed.success) return fallback
  return parsed.data
}

/**
 * Read settings from localStorage. The returned source separates a real user
 * override ('localStorage') from built-in defaults ('fallback'): callers such
 * as getEffectiveObservatorySettings treat only the former as authoritative.
 * SSR, missing storage, and malformed JSON all degrade to 'fallback'.
 */
export function loadStoredObservatorySettings():
  | { settings: ObservatorySettings; source: 'localStorage' }
  | { settings: ObservatorySettings; source: 'fallback' } {
  if (typeof window === 'undefined') {
    return { settings: getFallbackObservatorySettings(), source: 'fallback' }
  }

  const raw = window.localStorage.getItem(OBSERVATORY_SETTINGS_STORAGE_KEY)
  if (!raw) {
    return { settings: getFallbackObservatorySettings(), source: 'fallback' }
  }

  try {
    const parsed = parseObservatorySettings(JSON.parse(raw))
    return { settings: parsed, source: 'localStorage' }
  } catch {
    return { settings: getFallbackObservatorySettings(), source: 'fallback' }
  }
}

export function storeObservatorySettings(settings: ObservatorySettings): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(
    OBSERVATORY_SETTINGS_STORAGE_KEY,
    JSON.stringify(settings),
  )
}
