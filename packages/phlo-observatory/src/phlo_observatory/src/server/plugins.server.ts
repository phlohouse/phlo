/**
 * Plugins Server Functions
 *
 * Server-side functions for plugin discovery via CLI.
 */

import { exec } from 'node:child_process'
import { promisify } from 'node:util'
import { createServerFn } from '@tanstack/react-start'

const execAsync = promisify(exec)
const pluginCommand = process.env.PHLO_PLUGIN_COMMAND ?? 'phlo'
const registryUrl =
  process.env.PHLO_PLUGIN_REGISTRY_URL ?? 'https://phlohouse.com/plugins.json'
const defaultRegistryTimeoutMs = 5000
const defaultRegistryCacheTtlMs = 5 * 60 * 1000
const defaultRegistryStaleOnErrorMs = 24 * 60 * 60 * 1000

function parsePositiveDurationMs(
  rawValue: string | number | undefined,
  fallbackMs: number,
): number {
  const parsed = Number(rawValue ?? fallbackMs)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallbackMs
}

const registryTimeoutMsRaw = Number(
  process.env.PHLO_PLUGIN_REGISTRY_TIMEOUT_MS ?? defaultRegistryTimeoutMs,
)
const registryTimeoutMs = parsePositiveDurationMs(
  registryTimeoutMsRaw,
  defaultRegistryTimeoutMs,
)
const registryCacheTtlMs = parsePositiveDurationMs(
  process.env.PHLO_PLUGIN_REGISTRY_CACHE_TTL_MS,
  defaultRegistryCacheTtlMs,
)
const registryStaleOnErrorMs = parsePositiveDurationMs(
  process.env.PHLO_PLUGIN_REGISTRY_STALE_ON_ERROR_MS,
  defaultRegistryStaleOnErrorMs,
)

export interface PluginInfo {
  name: string
  type: 'source' | 'quality' | 'transform' | 'service' | 'observatory'
  version: string
  description?: string
  author?: string
  homepage?: string
  tags?: Array<string>
  installed?: boolean
  verified?: boolean
  core?: boolean
  package?: string
  category?: string
  profile?: string | null
  default?: boolean
}

interface RegistryPayload {
  plugins?: Record<
    string,
    Omit<PluginInfo, 'name' | 'type'> & { type?: PluginInfo['type'] }
  >
}

function parseCliOutput(stdout: string): {
  installed: Array<PluginInfo>
  available: Array<PluginInfo>
} {
  // Older CLI builds emit a bare array of installed plugins; the object form
  // additionally carries the registry-derived `available` list.
  const parsed = JSON.parse(stdout)
  if (Array.isArray(parsed)) {
    return { installed: parsed as Array<PluginInfo>, available: [] }
  }
  return {
    installed: parsed.installed ?? [],
    available: parsed.available ?? [],
  }
}

async function fetchRegistryPlugins(): Promise<Array<PluginInfo>> {
  const controller = new AbortController()
  const timeout = setTimeout(() => {
    controller.abort()
  }, registryTimeoutMs)
  try {
    const response = await fetch(registryUrl, { signal: controller.signal })
    if (!response.ok) {
      throw new Error(`Registry request failed: ${response.status}`)
    }
    const payload = (await response.json()) as RegistryPayload
    const entries = payload.plugins ?? {}
    return Object.entries(entries).map(([name, info]) => ({
      name,
      type: info.type ?? 'service',
      version: info.version ?? 'unknown',
      description: info.description,
      author: info.author,
      homepage: info.homepage,
      tags: info.tags,
      verified: info.verified,
      core: info.core,
      package: info.package,
    }))
  } finally {
    clearTimeout(timeout)
  }
}

/**
 * Single-flight registry fetcher.
 *
 * Concurrent callers share one in-flight request instead of stampeding the
 * remote registry. When a refresh fails, cached data is served for up to
 * staleOnErrorMs before the error is allowed to propagate; past that window
 * the failure surfaces so the UI does not show indefinitely stale plugins.
 */
export function createRegistryFetcherWithCache(dependencies: {
  fetchRegistry: () => Promise<Array<PluginInfo>>
  ttlMs: number
  staleOnErrorMs: number
  now?: () => number
}): () => Promise<Array<PluginInfo>> {
  let cache: { data: Array<PluginInfo>; fetchedAtMs: number } | null = null
  let inFlight: Promise<Array<PluginInfo>> | null = null

  const now = dependencies.now ?? Date.now
  const ttlMs = parsePositiveDurationMs(
    dependencies.ttlMs,
    defaultRegistryCacheTtlMs,
  )
  const staleOnErrorMs = parsePositiveDurationMs(
    dependencies.staleOnErrorMs,
    defaultRegistryStaleOnErrorMs,
  )

  return async () => {
    const currentTimeMs = now()
    if (cache && currentTimeMs - cache.fetchedAtMs < ttlMs) {
      return cache.data
    }

    if (inFlight) {
      return inFlight
    }

    inFlight = (async () => {
      try {
        const fresh = await dependencies.fetchRegistry()
        cache = { data: fresh, fetchedAtMs: now() }
        return fresh
      } catch (error) {
        const staleTimeMs = now()
        if (cache && staleTimeMs - cache.fetchedAtMs < staleOnErrorMs) {
          return cache.data
        }
        throw error
      } finally {
        inFlight = null
      }
    })()

    return inFlight
  }
}

const fetchRegistryPluginsCached = createRegistryFetcherWithCache({
  fetchRegistry: fetchRegistryPlugins,
  ttlMs: registryCacheTtlMs,
  staleOnErrorMs: registryStaleOnErrorMs,
})

export async function resolvePluginLists(dependencies: {
  runPluginListCommand: () => Promise<string>
  fetchRegistry: () => Promise<Array<PluginInfo>>
}): Promise<{
  installed: Array<PluginInfo>
  available: Array<PluginInfo>
}> {
  let installed: Array<PluginInfo> = []
  let available: Array<PluginInfo> = []

  try {
    const stdout = await dependencies.runPluginListCommand()
    const parsed = parseCliOutput(stdout)
    installed = parsed.installed
    available = parsed.available
  } catch {
    // Fall back to registry when CLI command is unavailable in the runtime image.
  }

  if (available.length > 0) {
    return { installed, available }
  }

  try {
    available = await dependencies.fetchRegistry()
  } catch {
    // Offline / DNS failures should not break the Plugins UI.
  }

  return { installed, available }
}

async function getPluginLists(): Promise<{
  installed: Array<PluginInfo>
  available: Array<PluginInfo>
}> {
  return resolvePluginLists({
    runPluginListCommand: async () => {
      const { stdout } = await execAsync(
        `${pluginCommand} plugin list --all --json`,
      )
      return stdout
    },
    fetchRegistry: fetchRegistryPluginsCached,
  })
}

export const getAvailablePlugins = createServerFn().handler(
  async (): Promise<{
    installed: Array<PluginInfo>
    available: Array<PluginInfo>
  }> => {
    return getPluginLists()
  },
)
