/**
 * Metadata Cache - see ADR 0019-observatory-metadata-caching.md
 */

const DEFAULT_TTL_MS = 5 * 60 * 1000
const TABLE_LIST_TTL_MS = 5 * 60 * 1000
const TABLE_SCHEMA_TTL_MS = 10 * 60 * 1000
const ASSET_LIST_TTL_MS = 2 * 60 * 1000
const SEARCH_INDEX_TTL_MS = 5 * 60 * 1000
const SERVICES_TTL_MS = 5 * 1000
const DAGSTER_HEALTH_TTL_MS = 5 * 1000
const DAGSTER_CONNECTION_TTL_MS = 10 * 1000
const NESSIE_CONNECTION_TTL_MS = 10 * 1000
const NESSIE_BRANCHES_TTL_MS = 10 * 1000
const NESSIE_BRANCH_TTL_MS = 10 * 1000
const NESSIE_COMMITS_TTL_MS = 5 * 1000
const NESSIE_CONTENTS_TTL_MS = 5 * 1000
const NESSIE_DIFF_TTL_MS = 5 * 1000
const LOKI_LABELS_TTL_MS = 10 * 1000
const LOKI_QUERY_TTL_MS = 5 * 1000
const MAINTENANCE_STATUS_TTL_MS = 10 * 1000
const QUALITY_OVERVIEW_TTL_MS = 10 * 1000
const QUALITY_FAILING_TTL_MS = 5 * 1000
const QUALITY_DASHBOARD_TTL_MS = 5 * 1000
const QUALITY_ASSET_CHECKS_TTL_MS = 10 * 1000
const QUALITY_CHECK_HISTORY_TTL_MS = 10 * 1000
const GRAPH_FULL_TTL_MS = 10 * 1000
const GRAPH_NEIGHBORS_TTL_MS = 10 * 1000
const MAX_CACHE_ENTRIES = 1000

interface CacheEntry<T> {
  value: T
  expiresAt: number
  createdAt: number
}

interface CacheStats {
  hits: number
  misses: number
  entries: number
  hitRate: number
  entriesByPrefix: Record<string, number>
}

// Process-local in-memory cache shared by all server functions in this
// runtime. Entries expire lazily on read; when MAX_CACHE_ENTRIES is reached
// the oldest entry (by creation time) is evicted before a new one is stored.
class MetadataCache {
  private cache = new Map<string, CacheEntry<unknown>>()
  private hits = 0
  private misses = 0

  get<T>(key: string): T | undefined {
    const entry = this.cache.get(key)

    if (!entry) {
      this.misses++
      this.logCacheEvent('miss', key)
      return undefined
    }

    if (Date.now() > entry.expiresAt) {
      this.cache.delete(key)
      this.misses++
      this.logCacheEvent('expired', key)
      return undefined
    }

    this.hits++
    this.logCacheEvent('hit', key)
    return entry.value as T
  }

  set<T>(key: string, value: T, ttlMs: number = DEFAULT_TTL_MS): void {
    if (this.cache.size >= MAX_CACHE_ENTRIES) {
      this.evictOldest()
    }

    const now = Date.now()
    this.cache.set(key, {
      value,
      expiresAt: now + ttlMs,
      createdAt: now,
    })
    this.logCacheEvent('set', key, ttlMs)
  }

  invalidate(key: string): boolean {
    const deleted = this.cache.delete(key)
    if (deleted) {
      this.logCacheEvent('invalidate', key)
    }
    return deleted
  }

  invalidatePattern(pattern: string): number {
    let count = 0
    for (const key of this.cache.keys()) {
      if (key.startsWith(pattern)) {
        this.cache.delete(key)
        count++
      }
    }
    if (count > 0) {
      this.logCacheEvent('invalidate-pattern', pattern, count)
    }
    return count
  }

  clear(): void {
    const size = this.cache.size
    this.cache.clear()
    this.hits = 0
    this.misses = 0
    this.logCacheEvent('clear', `${size} entries`)
  }

  getStats(): CacheStats {
    const entriesByPrefix: Record<string, number> = {}

    for (const key of this.cache.keys()) {
      const prefix = key.split(':')[0] || 'unknown'
      entriesByPrefix[prefix] = (entriesByPrefix[prefix] || 0) + 1
    }

    const total = this.hits + this.misses
    return {
      hits: this.hits,
      misses: this.misses,
      entries: this.cache.size,
      hitRate: total > 0 ? this.hits / total : 0,
      entriesByPrefix,
    }
  }

  private evictOldest(): void {
    let oldestKey: string | undefined
    let oldestTime = Infinity

    for (const [key, entry] of this.cache.entries()) {
      if (entry.createdAt < oldestTime) {
        oldestTime = entry.createdAt
        oldestKey = key
      }
    }

    if (oldestKey) {
      this.cache.delete(oldestKey)
      this.logCacheEvent('evict', oldestKey)
    }
  }

  private logCacheEvent(
    event: string,
    key: string,
    extra?: number | string,
  ): void {
    if (process.env.DEBUG_CACHE === 'true') {
      const msg =
        extra !== undefined
          ? `[cache] ${event}: ${key} (${extra})`
          : `[cache] ${event}: ${key}`
      console.debug(msg)
    }
  }
}

const metadataCache = new MetadataCache()

function normalizeUrl(url: string): string {
  try {
    const parsed = new URL(url)
    return `${parsed.host}${parsed.pathname}`.replace(/\//g, '_')
  } catch {
    return url.replace(/[^a-zA-Z0-9]/g, '_')
  }
}

export const cacheKeys = {
  tables: (branch: string) => `iceberg:tables:${branch}`,

  tableSchema: (catalog: string, schema: string, table: string) =>
    `iceberg:schema:${catalog}:${schema}:${table}`,

  assets: (dagsterUrl: string) => `dagster:assets:${normalizeUrl(dagsterUrl)}`,

  searchIndex: (dagsterUrl: string, trinoUrl: string) =>
    `search:index:${normalizeUrl(dagsterUrl)}:${normalizeUrl(trinoUrl)}`,

  services: () => 'services:list',

  dagsterHealth: (dagsterUrl: string) =>
    `dagster:health:${normalizeUrl(dagsterUrl)}`,

  dagsterConnection: (dagsterUrl: string) =>
    `dagster:connection:${normalizeUrl(dagsterUrl)}`,

  nessieConnection: () => 'nessie:connection',

  nessieBranches: () => 'nessie:branches',

  nessieBranch: (nessieUrl: string, branch: string) =>
    `nessie:branch:${normalizeUrl(nessieUrl)}:${branch}`,

  nessieCommits: (nessieUrl: string, branch: string, limit: number) =>
    `nessie:commits:${normalizeUrl(nessieUrl)}:${branch}:${limit}`,

  nessieContents: (
    nessieUrl: string,
    branch: string,
    prefix: string | undefined,
  ) => `nessie:contents:${normalizeUrl(nessieUrl)}:${branch}:${prefix ?? ''}`,

  nessieDiff: (nessieUrl: string, fromBranch: string, toBranch: string) =>
    `nessie:diff:${normalizeUrl(nessieUrl)}:${fromBranch}:${toBranch}`,

  lokiConnection: (lokiUrl: string) =>
    `loki:connection:${normalizeUrl(lokiUrl)}`,

  lokiLabels: (lokiUrl: string) => `loki:labels:${normalizeUrl(lokiUrl)}`,

  lokiQuery: (lokiUrl: string, queryKey: string) =>
    `loki:query:${normalizeUrl(lokiUrl)}:${queryKey}`,

  maintenanceStatus: () => 'maintenance:status',

  qualityOverview: () => 'quality:overview',

  qualityFailing: () => 'quality:failing',

  qualityDashboard: () => 'quality:dashboard',

  qualityAssetChecks: (assetKey: string) => `quality:checks:${assetKey}`,

  qualityCheckHistory: (assetKey: string, checkName: string, limit: number) =>
    `quality:history:${assetKey}:${checkName}:${limit}`,

  graphFull: () => 'graph:full',

  graphNeighbors: (assetKey: string, direction: string, depth: number) =>
    `graph:neighbors:${assetKey}:${direction}:${depth}`,
}

export const cacheTTL = {
  tables: TABLE_LIST_TTL_MS,
  tableSchema: TABLE_SCHEMA_TTL_MS,
  assets: ASSET_LIST_TTL_MS,
  searchIndex: SEARCH_INDEX_TTL_MS,
  services: SERVICES_TTL_MS,
  dagsterHealth: DAGSTER_HEALTH_TTL_MS,
  dagsterConnection: DAGSTER_CONNECTION_TTL_MS,
  nessieConnection: NESSIE_CONNECTION_TTL_MS,
  nessieBranches: NESSIE_BRANCHES_TTL_MS,
  nessieBranch: NESSIE_BRANCH_TTL_MS,
  nessieCommits: NESSIE_COMMITS_TTL_MS,
  nessieContents: NESSIE_CONTENTS_TTL_MS,
  nessieDiff: NESSIE_DIFF_TTL_MS,
  lokiLabels: LOKI_LABELS_TTL_MS,
  lokiQuery: LOKI_QUERY_TTL_MS,
  maintenanceStatus: MAINTENANCE_STATUS_TTL_MS,
  qualityOverview: QUALITY_OVERVIEW_TTL_MS,
  qualityFailing: QUALITY_FAILING_TTL_MS,
  qualityDashboard: QUALITY_DASHBOARD_TTL_MS,
  qualityAssetChecks: QUALITY_ASSET_CHECKS_TTL_MS,
  qualityCheckHistory: QUALITY_CHECK_HISTORY_TTL_MS,
  graphFull: GRAPH_FULL_TTL_MS,
  graphNeighbors: GRAPH_NEIGHBORS_TTL_MS,
  default: DEFAULT_TTL_MS,
}

function isErrorResult(result: unknown): boolean {
  return (
    typeof result === 'object' &&
    result !== null &&
    'error' in result &&
    typeof (result as { error: unknown }).error === 'string'
  )
}

export async function withCache<T>(
  fn: () => Promise<T>,
  key: string,
  ttlMs: number = DEFAULT_TTL_MS,
): Promise<T> {
  const cached = metadataCache.get<T>(key)
  if (cached !== undefined) {
    return cached
  }

  const result = await fn()

  // Cache only defined, non-error results: an undefined value or an
  // `{ error: string }` payload is returned uncached so transient backend
  // failures are retried on the next request.
  if (result !== undefined && !isErrorResult(result)) {
    metadataCache.set(key, result, ttlMs)
  }

  return result
}

export function getCacheStats(): CacheStats {
  return metadataCache.getStats()
}

export function invalidateCache(keyOrPattern: string): number {
  // A trailing '*' selects prefix matching; anything else must match exactly.
  if (keyOrPattern.endsWith('*')) {
    return metadataCache.invalidatePattern(keyOrPattern.slice(0, -1))
  }
  return metadataCache.invalidate(keyOrPattern) ? 1 : 0
}

export function clearCache(): void {
  metadataCache.clear()
}
