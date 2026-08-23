/**
 * Client-side live resource cache for Observatory pages.
 *
 * useLiveResource polls a loader on an interval (and when the tab regains
 * focus) and serves every consumer from one shared cache keyed by resource
 * key. Successful results are kept in memory and mirrored into sessionStorage
 * so a reload or client-side navigation starts from the last known data.
 *
 * Failure policy: errors are never cached as fresh data. When a refresh fails
 * but stale data exists, the stale data is returned with the new error
 * attached (stale-on-error). Only results without useful data are dropped.
 */
import { useEffect, useMemo, useState } from 'react'

import type { ObservatoryResourceResult } from '@/observatory/api/types'

declare global {
  interface Window {
    __PHLO_API_BROWSER_URL__?: string
  }
}

type CachedEntry<T> = {
  expiresAt: number
  promise: Promise<ObservatoryResourceResult<T>> | null
  result: ObservatoryResourceResult<T>
}

type LiveRefreshMode = 'preserve' | 'reset'
type PersistedEntry<T> = {
  expiresAt: number
  result: ObservatoryResourceResult<T>
}

const resourceCache = new Map<string, CachedEntry<unknown>>()
const resourceKeys = new WeakMap<object, string>()
// Bumping cacheVersion drops every memory and persisted entry at once after
// a cached-shape change; old sessionStorage entries become unreachable keys.
const cacheVersion = '2026-07-10-observatory-runtime-v11'
const persistentCachePrefix = `phlo-observatory:${cacheVersion}`
const minPersistentTtlMs = 5 * 60_000
let nextResourceKey = 0

export function useLiveResource<T>(
  load: () => Promise<ObservatoryResourceResult<Array<T>>>,
  intervalMs = 60_000,
  cacheKey?: string,
) {
  const key = useMemo(
    () => cacheKey ?? stableResourceKey(load),
    [cacheKey, load],
  )
  const [result, setResult] = useState<ObservatoryResourceResult<Array<T>>>(
    () => ({ data: null, error: null }),
  )

  useEffect(() => {
    let cancelled = false
    const cached = readCachedResource<Array<T>>(key)
    if (cached) setResult(cached)

    async function refresh(force = false, mode: LiveRefreshMode = 'preserve') {
      if (force && mode === 'reset' && !cancelled) {
        setResult({ data: null, error: null })
      }
      const next = await loadCachedResource<Array<T>>(key, load, {
        force,
        staleMs: intervalMs,
      })
      if (!cancelled) setResult(next)
    }

    const refreshActiveTab = () => {
      if (document.visibilityState !== 'hidden') {
        void refresh(true)
      }
    }

    void refresh(true)
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') {
        void refresh(true)
      }
    }, intervalMs)
    window.addEventListener('focus', refreshActiveTab)
    document.addEventListener('visibilitychange', refreshActiveTab)

    return () => {
      cancelled = true
      window.clearInterval(interval)
      window.removeEventListener('focus', refreshActiveTab)
      document.removeEventListener('visibilitychange', refreshActiveTab)
    }
  }, [intervalMs, key, load])

  return {
    ...result,
    isLoading: result.data === null && !result.error,
  }
}

export function readCachedResource<T>(
  key: string,
): ObservatoryResourceResult<T> | null {
  const versionedKey = `${cacheVersion}:${key}`
  const cached = resourceCache.get(versionedKey) as CachedEntry<T> | undefined
  if (cached?.result) return cached.result

  const persisted = readPersistentResource<T>(key)
  if (!persisted) return null

  resourceCache.set(versionedKey, {
    expiresAt: persisted.expiresAt,
    promise: null,
    result: persisted.result,
  })
  return persisted.result
}

export async function loadCachedResource<T>(
  key: string,
  load: () => Promise<ObservatoryResourceResult<T>>,
  {
    force = false,
    staleMs = 60_000,
  }: {
    force?: boolean
    staleMs?: number
  } = {},
): Promise<ObservatoryResourceResult<T>> {
  const versionedKey = `${cacheVersion}:${key}`
  const persisted = readPersistentResource<T>(key)
  const cached =
    (resourceCache.get(versionedKey) as CachedEntry<T> | undefined) ??
    (persisted
      ? {
          expiresAt: persisted.expiresAt,
          promise: null,
          result: persisted.result,
        }
      : undefined)
  const now = Date.now()

  if (!force && cached?.result && cached.expiresAt > now) {
    return cached.result
  }
  if (cached?.promise) return cached.promise

  const promise = load().then(async (result) => {
    const fallback = await browserFallbackResource<T>(key)
    // A browser-direct retry of the same endpoint wins over the server result
    // whenever it produced useful data: it reflects fresher state than a
    // server-side fetch that may have failed during SSR.
    const loaded = isCacheableResult(fallback) ? fallback : result

    return Promise.resolve(loaded).then((nextResult) => {
      if (isCacheableResult(nextResult)) {
        const expiresAt = Date.now() + staleMs
        resourceCache.set(versionedKey, {
          expiresAt,
          promise: null,
          result: nextResult,
        })
        writePersistentResource(key, nextResult, expiresAt, staleMs)
      } else if (cached?.result) {
        const staleResult = staleResultWithError(cached.result, nextResult)
        resourceCache.set(versionedKey, {
          expiresAt: Date.now(),
          promise: null,
          result: staleResult,
        })
        return staleResult
      } else {
        resourceCache.delete(versionedKey)
      }
      return nextResult
    })
  })

  resourceCache.set(versionedKey, {
    expiresAt: cached?.expiresAt ?? 0,
    promise,
    result: cached?.result ?? { data: null, error: null },
  })

  return promise
}

async function browserFallbackResource<T>(
  key: string,
): Promise<ObservatoryResourceResult<T>> {
  if (typeof window === 'undefined') return { data: null, error: null }
  const base = browserApiBase()
  const endpoint = fallbackEndpoint(key)
  if (base === null || !endpoint) return { data: null, error: null }

  try {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 15_000)
    const response = await fetch(`${base}${endpoint}`, {
      signal: controller.signal,
    })
    window.clearTimeout(timeout)
    if (!response.ok) {
      return {
        data: null,
        error: `phlo-api error: ${response.status} ${response.statusText}`,
      }
    }
    const payload = await response.json()
    return {
      data: normalizeFallbackPayload<T>(key, payload),
      error: null,
    }
  } catch (error) {
    return {
      data: null,
      error:
        error instanceof Error ? error.message : 'Lakehouse API is unavailable',
    }
  }
}

function browserApiBase(): string | null {
  const configured =
    window.__PHLO_API_BROWSER_URL__ ??
    document.querySelector<HTMLMetaElement>('meta[name="phlo-api-browser-url"]')
      ?.content
  return configured?.trim() ?? ''
}

function fallbackEndpoint(key: string): string | null {
  const prefix = '/api/observatory'
  const tablePreviewEndpoint = tablePreviewFallbackEndpoint(key, prefix)
  if (tablePreviewEndpoint) return tablePreviewEndpoint

  const endpoints: Record<string, string> = {
    'observatory:overview': `${prefix}/overview`,
    'observatory:capabilities': `${prefix}/surface-capabilities`,
    'observatory:services': `${prefix}/services`,
    'observatory:operations': `${prefix}/operations`,
    'observatory:runs': `${prefix}/runs`,
    'observatory:pipelines': `${prefix}/pipelines`,
    'observatory:storage': `${prefix}/storage`,
    'observatory:observability': `${prefix}/observability`,
    'observatory:governance': `${prefix}/governance`,
    'observatory:governance-matrix': `${prefix}/governance`,
    'observatory:apis': `${prefix}/apis`,
    'observatory:bi': `${prefix}/bi`,
    'observatory:assets': `${prefix}/assets`,
    'observatory:tables': `${prefix}/tables`,
    'observatory:saved-queries': `${prefix}/saved-queries`,
    'observatory:quality': `${prefix}/quality`,
    'observatory:logs': `${prefix}/logs`,
    'observatory:branches': `${prefix}/branches`,
    'observatory:extensions': `${prefix}/extensions`,
    'observatory:workflow-wizard': `${prefix}/workflow-wizard`,
  }
  return endpoints[key] ?? null
}

function tablePreviewFallbackEndpoint(
  key: string,
  prefix: string,
): string | null {
  const tablePreviewPrefix = 'observatory:table-preview:'
  if (!key.startsWith(tablePreviewPrefix)) return null

  const parts = key.slice(tablePreviewPrefix.length).split(':')
  if (parts.length < 4) return null

  parts.pop()
  const offset = parts.pop()
  const limit = parts.pop()
  const tableId = parts.join(':')
  if (!tableId || !limit || !offset) return null

  const searchParams = new URLSearchParams({ limit, offset })
  return `${prefix}/table-preview/${encodeURIComponent(tableId)}?${searchParams}`
}

function normalizeFallbackPayload<T>(key: string, payload: unknown): T {
  if (
    key === 'observatory:branches' &&
    isRecord(payload) &&
    Array.isArray(payload.items)
  ) {
    return payload.items.map((branch) => normalizeBranchFallback(branch)) as T
  }

  if (isRecord(payload) && Array.isArray(payload.items)) {
    return payload.items as T
  }

  return payload as T
}

function normalizeBranchFallback(value: unknown): Record<string, unknown> {
  const branch = isRecord(value) ? value : {}
  const id = safeString(branch.id) ?? safeString(branch.name) ?? 'branch'
  const name = safeString(branch.name) ?? id
  const current = branch.current === true
  return {
    id,
    name,
    current,
    kind: 'branch',
    protected: branch.protected === true,
    status: current ? 'current' : 'branch',
    summary: current ? 'Current branch' : 'Branch',
    metadata: isRecord(branch.metadata) ? branch.metadata : {},
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function safeString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

export function invalidateCachedResource(key: string): void {
  resourceCache.delete(`${cacheVersion}:${key}`)
  removePersistentResource(key)
}

export function invalidateCachedResources(keys: Array<string>): void {
  for (const key of keys) {
    invalidateCachedResource(key)
  }
}

function isCacheableResult<T>(result: ObservatoryResourceResult<T>): boolean {
  if (result.error) return false
  return hasUsefulData(result.data)
}

function staleResultWithError<T>(
  cached: ObservatoryResourceResult<T>,
  failed: ObservatoryResourceResult<T>,
): ObservatoryResourceResult<T> {
  if (!hasUsefulData(cached.data)) return failed
  return {
    data: cached.data,
    error: failed.error ?? 'Showing last known lakehouse data.',
  }
}

function hasUsefulData(data: unknown): boolean {
  if (data === null || data === undefined) return false
  if (Array.isArray(data)) return data.length > 0
  if (typeof data !== 'object') return true

  if ('health' in data && 'counters' in data) {
    return true
  }

  if ('items' in data && Array.isArray(data.items)) return data.items.length > 0
  if ('pages' in data && Array.isArray(data.pages)) return data.pages.length > 0
  if ('columns' in data && Array.isArray(data.columns)) return true
  if ('rows' in data && Array.isArray(data.rows)) return true

  return true
}

// Loaders without an explicit cacheKey get a stable synthetic key derived
// from function identity, so re-renders reuse the same cache entry instead
// of re-fetching.
function stableResourceKey(load: object): string {
  const existing = resourceKeys.get(load)
  if (existing) return existing
  const key = `resource:${nextResourceKey}`
  nextResourceKey += 1
  resourceKeys.set(load, key)
  return key
}

function persistentStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage ?? null
  } catch {
    return null
  }
}

function persistentKey(key: string): string {
  return `${persistentCachePrefix}:${key}`
}

function readPersistentResource<T>(key: string): PersistedEntry<T> | null {
  const storage = persistentStorage()
  if (!storage) return null

  try {
    const raw = storage.getItem(persistentKey(key))
    if (!raw) return null
    const parsed = JSON.parse(raw) as PersistedEntry<T>
    if (!parsed || parsed.expiresAt <= Date.now()) {
      storage.removeItem(persistentKey(key))
      return null
    }
    if (!isCacheableResult(parsed.result)) {
      storage.removeItem(persistentKey(key))
      return null
    }
    return parsed
  } catch {
    storage.removeItem(persistentKey(key))
    return null
  }
}

function writePersistentResource<T>(
  key: string,
  result: ObservatoryResourceResult<T>,
  memoryExpiresAt: number,
  staleMs: number,
): void {
  const storage = persistentStorage()
  if (!storage) return

  // Persisted entries live at least minPersistentTtlMs even when the
  // in-memory TTL is shorter, so navigating back within the session can
  // restore data.
  const expiresAt = Math.max(
    memoryExpiresAt,
    Date.now() + Math.max(staleMs, minPersistentTtlMs),
  )
  try {
    storage.setItem(
      persistentKey(key),
      JSON.stringify({
        expiresAt,
        result,
      } satisfies PersistedEntry<T>),
    )
  } catch {
    removePersistentResource(key)
  }
}

function removePersistentResource(key: string): void {
  const storage = persistentStorage()
  if (!storage) return
  try {
    storage.removeItem(persistentKey(key))
  } catch {
    // Ignore storage failures; the in-memory cache has already been cleared.
  }
}

export function readMetric(
  metadata: Record<string, unknown>,
  key: string,
): string | number | null {
  const value = metadata[key]
  if (typeof value === 'string' || typeof value === 'number') {
    return value
  }
  return null
}
