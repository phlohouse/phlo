/**
 * Quality snapshot from Dagster.
 *
 * Queries Dagster's GraphQL API for asset check definitions and their recent
 * executions, then normalizes both into the QualityCheck/RecentCheckExecution
 * shapes used by the UI. Dagster never throws here: every failure mode
 * (HTTP, GraphQL errors, timeouts) is returned as `{ error: string }`.
 */
import type {
  MetadataValue,
  QualityCheck,
  RecentCheckExecution,
} from '@/server/quality.types'

type DagsterGraphQLError = { message?: string }
type DagsterGraphQlResponse<TData> = {
  data?: TData
  errors?: Array<DagsterGraphQLError>
}

type DagsterMetadataEntry =
  | { label: string; __typename: 'TextMetadataEntry'; text: string }
  | { label: string; __typename: 'IntMetadataEntry'; intValue: number }
  | { label: string; __typename: 'FloatMetadataEntry'; floatValue: number }
  | { label: string; __typename: 'BoolMetadataEntry'; boolValue: boolean }
  | { label: string; __typename: 'JsonMetadataEntry'; jsonString: string }
  | { label: string; __typename: string }

type DagsterAssetNode = {
  assetKey: { path: Array<string> }
  assetChecksOrError:
    | {
        __typename: 'AssetChecks'
        checks: Array<{ name: string; description?: string | null }>
      }
    | { __typename: string; message?: string }
}

type DagsterAssetCheckExecution = {
  status: string
  runId?: string | null
  timestamp: string | number
  checkName: string
  evaluation?: {
    severity?: 'ERROR' | 'WARN' | null
    metadataEntries?: Array<DagsterMetadataEntry> | null
  } | null
}

const ASSET_CHECKS_QUERY = `
  query AssetChecksQuery {
    assetNodes {
      assetKey {
        path
      }
      assetChecksOrError {
        __typename
        ... on AssetChecks {
          checks {
            name
            description
          }
        }
        ... on AssetCheckNeedsMigrationError {
          message
        }
      }
    }
  }
`

const ASSET_CHECK_EXECUTIONS_QUERY = `
  query AssetCheckExecutionsQuery($assetKey: AssetKeyInput!, $limit: Int!) {
    assetCheckExecutions(assetKey: $assetKey, limit: $limit) {
      status
      runId
      timestamp
      checkName
      evaluation {
        severity
        metadataEntries {
          __typename
          label
          ... on TextMetadataEntry {
            text
          }
          ... on IntMetadataEntry {
            intValue
          }
          ... on FloatMetadataEntry {
            floatValue
          }
          ... on BoolMetadataEntry {
            boolValue
          }
          ... on JsonMetadataEntry {
            jsonString
          }
        }
      }
    }
  }
`

const CACHE_TTL_MS = 10_000
const snapshotCache = new Map<string, { expiresAtMs: number; value: unknown }>()

const DEFAULT_DAGSTER_URL = 'http://localhost:3000/graphql'

function resolveDagsterUrl(override?: string): string {
  if (override && override.trim()) return override
  return process.env.DAGSTER_GRAPHQL_URL || DEFAULT_DAGSTER_URL
}

export type QualitySnapshot = {
  totalChecks: number
  passingChecks: number
  failingChecks: number
  warningChecks: number
  latestChecks: Array<QualityCheck>
  failingChecksList: Array<QualityCheck>
  recentExecutions: Array<RecentCheckExecution>
}

/**
 * Build the full quality snapshot.
 *
 * Asset checks are listed first, then recent executions are fetched for every
 * asset in parallel. Any single failure aborts the whole snapshot — partial
 * results are never returned or cached. Checks with no recorded execution are
 * omitted from all counts. Snapshots are cached per Dagster URL for
 * CACHE_TTL_MS because the dashboard polls this on every render.
 */
export async function fetchQualitySnapshot(options?: {
  dagsterUrl?: string
  recentLimit?: number
  timeoutMs?: number
}): Promise<QualitySnapshot | { error: string }> {
  const recentLimit = options?.recentLimit ?? 50
  const timeoutMs = options?.timeoutMs ?? 10_000

  const dagsterUrl = resolveDagsterUrl(options?.dagsterUrl)
  const cacheKey = `quality_snapshot:${dagsterUrl}`
  const cached = getCached<QualitySnapshot>(cacheKey)
  if (cached) return cached

  const assetsResponse = await dagsterQuery<{
    assetNodes: Array<DagsterAssetNode>
  }>(dagsterUrl, {
    query: ASSET_CHECKS_QUERY,
    variables: {},
    timeoutMs,
  })
  if ('error' in assetsResponse) return assetsResponse

  const assetNodes = assetsResponse.data?.assetNodes ?? []
  const assetsWithChecks: Array<{
    assetKey: Array<string>
    checks: Array<{ name: string; description?: string | null }>
  }> = []

  for (const node of assetNodes) {
    const checksOrError = node.assetChecksOrError
    if (checksOrError?.__typename !== 'AssetChecks') continue
    const checks = (
      checksOrError as {
        checks?: Array<{ name: string; description?: string | null }>
      }
    ).checks
    if (!checks?.length) continue
    assetsWithChecks.push({ assetKey: node.assetKey.path, checks })
  }

  const executionFetches: Array<
    Promise<
      | {
          assetKey: Array<string>
          executions: Array<DagsterAssetCheckExecution>
        }
      | { error: string }
    >
  > = []

  for (const asset of assetsWithChecks) {
    const perAssetLimit = Math.max(50, asset.checks.length * 3)
    executionFetches.push(
      fetchAssetCheckExecutions(
        dagsterUrl,
        asset.assetKey,
        perAssetLimit,
        timeoutMs,
      ).then((result) => {
        if ('error' in result) return result
        return { assetKey: asset.assetKey, executions: result }
      }),
    )
  }

  const executionResults = await Promise.all(executionFetches)
  for (const result of executionResults) {
    if ('error' in result) return result
  }

  let totalChecks = 0
  let passingChecks = 0
  let failingChecks = 0
  let warningChecks = 0
  const latestChecks: Array<QualityCheck> = []
  const failingChecksList: Array<QualityCheck> = []
  const recentExecutions: Array<RecentCheckExecution> = []

  for (let index = 0; index < assetsWithChecks.length; index += 1) {
    const asset = assetsWithChecks[index]
    const execs = (
      executionResults[index] as {
        executions: Array<DagsterAssetCheckExecution>
      }
    ).executions

    totalChecks += asset.checks.length

    const latestByCheck = newestExecutionByCheck(execs)

    for (const checkDef of asset.checks) {
      const latest = latestByCheck.get(checkDef.name)
      if (!latest) continue

      const status = normalize_execution_status(latest.status)
      const severity = normalize_severity(latest.evaluation?.severity)

      const check: QualityCheck = {
        name: checkDef.name,
        assetKey: asset.assetKey,
        description: checkDef.description ?? undefined,
        severity,
        status,
        lastExecutionTime: to_iso_timestamp(latest.timestamp),
        lastResult: {
          passed: status === 'PASSED',
          metadata: metadata_entries_to_record(
            latest.evaluation?.metadataEntries,
          ),
        },
      }

      latestChecks.push(check)

      if (status === 'PASSED') {
        passingChecks += 1
      } else if (status === 'FAILED' && severity === 'WARN') {
        warningChecks += 1
      } else if (status === 'FAILED') {
        failingChecks += 1
        failingChecksList.push(check)
      }
    }

    for (const exec of execs) {
      recentExecutions.push({
        assetKey: asset.assetKey,
        checkName: exec.checkName,
        timestamp: to_iso_timestamp(exec.timestamp),
        passed: normalize_execution_status(exec.status) === 'PASSED',
        runId: exec.runId ?? undefined,
        severity: normalize_severity(exec.evaluation?.severity),
        status: normalize_execution_status(exec.status),
        metadata: metadata_entries_to_record(exec.evaluation?.metadataEntries),
      })
    }
  }

  recentExecutions.sort((a, b) =>
    compare_timestamp_desc(a.timestamp, b.timestamp),
  )
  const value: QualitySnapshot = {
    totalChecks,
    passingChecks,
    failingChecks,
    warningChecks,
    latestChecks,
    failingChecksList,
    recentExecutions: recentExecutions.slice(0, recentLimit),
  }

  setCached(cacheKey, value, CACHE_TTL_MS)
  return value
}

async function dagsterQuery<TData>(
  dagsterUrl: string,
  options: { query: string; variables: object; timeoutMs: number },
): Promise<DagsterGraphQlResponse<TData> | { error: string }> {
  try {
    const response = await fetch(dagsterUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: options.query,
        variables: options.variables,
      }),
      signal: AbortSignal.timeout(options.timeoutMs),
    })

    if (!response.ok) {
      return { error: `HTTP ${response.status}: ${response.statusText}` }
    }

    const result = (await response.json()) as DagsterGraphQlResponse<TData>
    if (result.errors?.length) {
      return { error: result.errors[0]?.message || 'GraphQL error' }
    }
    return result
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Unknown error' }
  }
}

async function fetchAssetCheckExecutions(
  dagsterUrl: string,
  assetKey: Array<string>,
  limit: number,
  timeoutMs: number,
): Promise<Array<DagsterAssetCheckExecution> | { error: string }> {
  const result = await dagsterQuery<{
    assetCheckExecutions: Array<DagsterAssetCheckExecution>
  }>(dagsterUrl, {
    query: ASSET_CHECK_EXECUTIONS_QUERY,
    variables: { assetKey: { path: assetKey }, limit },
    timeoutMs,
  })
  if ('error' in result) return result
  return result.data?.assetCheckExecutions ?? []
}

function newestExecutionByCheck(
  executions: Array<DagsterAssetCheckExecution>,
): Map<string, DagsterAssetCheckExecution> {
  const newest = new Map<string, DagsterAssetCheckExecution>()
  for (const exec of executions) {
    const existing = newest.get(exec.checkName)
    if (!existing) {
      newest.set(exec.checkName, exec)
      continue
    }
    if (compare_timestamp_desc(exec.timestamp, existing.timestamp) < 0) continue
    newest.set(exec.checkName, exec)
  }
  return newest
}

function compare_timestamp_desc(
  a: string | number,
  b: string | number,
): number {
  const aMs = to_epoch_ms(a)
  const bMs = to_epoch_ms(b)
  return bMs - aMs
}

function to_epoch_ms(value: string | number): number {
  if (typeof value === 'number') {
    if (value > 1_000_000_000_000) return value
    return value * 1000
  }
  const trimmed = value.trim()
  if (!trimmed) return 0
  const asNum = Number(trimmed)
  if (!Number.isNaN(asNum) && Number.isFinite(asNum)) {
    if (asNum > 1_000_000_000_000) return asNum
    return asNum * 1000
  }
  const asDateMs = Date.parse(trimmed)
  if (!Number.isNaN(asDateMs)) return asDateMs
  // Unparsable timestamps collapse to epoch 0 (1970) rather than NaN so
  // downstream sorting and ISO conversion stay total.
  return 0
}

function to_iso_timestamp(value: string | number): string {
  return new Date(to_epoch_ms(value)).toISOString()
}

function normalize_execution_status(
  status: string,
): RecentCheckExecution['status'] {
  const normalized = status.trim().toUpperCase()
  if (normalized === 'SUCCEEDED') return 'PASSED'
  if (normalized === 'FAILED') return 'FAILED'
  if (normalized === 'IN_PROGRESS') return 'IN_PROGRESS'
  return 'SKIPPED'
}

function normalize_severity(
  severity: 'ERROR' | 'WARN' | null | undefined,
): 'ERROR' | 'WARN' {
  if (severity === 'WARN') return 'WARN'
  return 'ERROR'
}

function metadata_entries_to_record(
  entries: Array<DagsterMetadataEntry> | null | undefined,
): Record<string, MetadataValue> {
  const record: Record<string, MetadataValue> = {}
  if (!entries) return record

  for (const entry of entries) {
    if (!entry.label) continue
    if (entry.__typename === 'TextMetadataEntry' && 'text' in entry) {
      record[entry.label] = entry.text
      continue
    }
    if (entry.__typename === 'IntMetadataEntry' && 'intValue' in entry) {
      record[entry.label] = entry.intValue
      continue
    }
    if (entry.__typename === 'FloatMetadataEntry' && 'floatValue' in entry) {
      record[entry.label] = entry.floatValue
      continue
    }
    if (entry.__typename === 'BoolMetadataEntry' && 'boolValue' in entry) {
      record[entry.label] = entry.boolValue
      continue
    }
    if (entry.__typename === 'JsonMetadataEntry' && 'jsonString' in entry) {
      try {
        record[entry.label] = JSON.parse(entry.jsonString) as MetadataValue
      } catch {
        record[entry.label] = entry.jsonString
      }
      continue
    }
  }

  return record
}

function getCached<T>(key: string): T | null {
  const cached = snapshotCache.get(key)
  if (!cached) return null
  if (Date.now() >= cached.expiresAtMs) {
    snapshotCache.delete(key)
    return null
  }
  return cached.value as T
}

function setCached(key: string, value: unknown, ttlMs: number): void {
  snapshotCache.set(key, { expiresAtMs: Date.now() + ttlMs, value })
}
