/**
 * The lakehouse map — home posture. Aggregates overview, assets, datasets,
 * pipelines, services, operations, quality, and logs into one
 * reducer-driven snapshot; the graph, attention strip, and selection dock
 * render from it. Selection lives in the ?node= search param so map state
 * is shareable. loadOverviewSnapshotFromApi lets the route loader pre-fetch
 * the full state.
 */
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useReducer } from 'react'

import type {
  ObservatoryAsset,
  ObservatoryCapabilities,
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryLogEvent,
  ObservatoryOperation,
  ObservatoryOverview,
  ObservatoryOverviewRow,
  ObservatoryQualityCheck,
  ObservatoryResourceResult,
  ObservatoryService,
} from '@/observatory/api/types'
import {
  getObservatoryAssetRecords,
  getObservatoryCapabilities,
  getObservatoryDatasetRecords,
  getObservatoryLogRecords,
  getObservatoryOperationRecords,
  getObservatoryOverview,
  getObservatoryPipelineRecords,
  getObservatoryQualityRecords,
  getObservatoryServices,
} from '@/observatory/api/resources'
import { loadCachedResource } from '@/observatory/routes/liveResource'
import { LakehouseMap } from '@/components/map/lakehouse-map'
import { buildLakehouseMap } from '@/components/map/map-model'
import { NodeDock } from '@/components/map/node-dock'
import {
  HealthDot,
  StatusBadge,
  statusStateFor,
} from '@/components/observatory/status'
import { formatRelativeTime } from '@/components/observatory/time'
import { cn } from '@/lib/utils'

const formatter = new Intl.NumberFormat('en')

type OverviewState = {
  assets: ObservatoryResourceResult<Array<ObservatoryAsset>>
  capabilities: ObservatoryResourceResult<ObservatoryCapabilities> | null
  datasets: ObservatoryResourceResult<Array<ObservatoryDataset>>
  logs: ObservatoryResourceResult<Array<ObservatoryLogEvent>>
  operations: ObservatoryResourceResult<Array<ObservatoryOperation>>
  overview: ObservatoryResourceResult<ObservatoryOverview>
  pipelines: ObservatoryResourceResult<Array<ObservatoryDatasetPipeline>>
  quality: ObservatoryResourceResult<Array<ObservatoryQualityCheck>>
  services: ObservatoryResourceResult<Array<ObservatoryService>>
  updatedAt: Date | null
}

export type OverviewSnapshot = Omit<OverviewState, 'updatedAt'> & {
  updatedAt: string | null
}

function overviewReducer(
  state: OverviewState,
  patch: Partial<OverviewState>,
): OverviewState {
  return {
    ...state,
    ...patch,
  }
}

export function loadOverviewSnapshot(): OverviewSnapshot {
  const empty = { data: [], error: null }
  const pending = { data: null, error: null }

  return {
    assets: empty,
    capabilities: null,
    datasets: empty,
    logs: empty,
    operations: empty,
    overview: pending,
    pipelines: empty,
    quality: empty,
    services: empty,
    updatedAt: null,
  }
}

export async function loadOverviewSnapshotFromApi(): Promise<OverviewSnapshot> {
  const [
    overview,
    services,
    operations,
    assets,
    quality,
    logs,
    datasets,
    pipelines,
    capabilities,
  ] = await Promise.all([
    getObservatoryOverview(),
    getObservatoryServices(),
    getObservatoryOperationRecords(),
    getObservatoryAssetRecords(),
    getObservatoryQualityRecords(),
    getObservatoryLogRecords(),
    getObservatoryDatasetRecords(),
    getObservatoryPipelineRecords(),
    getObservatoryCapabilities(),
  ])

  return {
    assets,
    capabilities,
    datasets,
    logs,
    operations,
    overview,
    pipelines,
    quality,
    services,
    updatedAt: new Date().toISOString(),
  }
}

export function OverviewRoute({
  initialSnapshot,
}: {
  initialSnapshot?: OverviewSnapshot
}) {
  return useOverviewRoute(initialSnapshot)
}

function useOverviewRoute(initialSnapshot?: OverviewSnapshot) {
  const [
    {
      assets,
      capabilities,
      datasets,
      logs,
      operations,
      overview,
      pipelines,
      quality,
      services,
      updatedAt,
    },
    setOverviewState,
  ] = useReducer(overviewReducer, {
    assets: initialSnapshot?.assets ?? { data: null, error: null },
    capabilities: initialSnapshot?.capabilities ?? null,
    datasets: initialSnapshot?.datasets ?? { data: null, error: null },
    logs: initialSnapshot?.logs ?? { data: null, error: null },
    operations: initialSnapshot?.operations ?? { data: null, error: null },
    overview: initialSnapshot?.overview ?? { data: null, error: null },
    pipelines: initialSnapshot?.pipelines ?? { data: null, error: null },
    quality: initialSnapshot?.quality ?? { data: null, error: null },
    services: initialSnapshot?.services ?? { data: null, error: null },
    updatedAt: initialSnapshot?.updatedAt
      ? new Date(initialSnapshot.updatedAt)
      : null,
  })

  useEffect(() => {
    let cancelled = false

    function load(force = false) {
      const requests: Array<
        [
          keyof Omit<OverviewState, 'updatedAt'>,
          () => Promise<ObservatoryResourceResult<unknown>>,
          number,
        ]
      > = [
        ['services', getObservatoryServices, 60_000],
        ['operations', getObservatoryOperationRecords, 60_000],
        ['assets', getObservatoryAssetRecords, 60_000],
        ['quality', getObservatoryQualityRecords, 60_000],
        ['logs', getObservatoryLogRecords, 30_000],
        ['datasets', getObservatoryDatasetRecords, 60_000],
        ['pipelines', getObservatoryPipelineRecords, 60_000],
        ['capabilities', getObservatoryCapabilities, 120_000],
        ['overview', getObservatoryOverview, 30_000],
      ]
      for (const [field, loader, staleMs] of requests) {
        void loadCachedResource(`observatory:${field}`, loader, {
          force,
          staleMs,
        }).then((next) => {
          if (cancelled) return
          setOverviewState({
            [field]: next,
            updatedAt: new Date(),
          } as Partial<OverviewState>)
        })
      }
    }

    load(true)
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') load(true)
    }, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  const serviceRows = services.data ?? []
  const operationRows = operations.data ?? []
  const assetRows = assets.data ?? []
  const qualityRows = quality.data ?? []
  const logRows = logs.data ?? []
  const datasetRows = datasets.data ?? []
  const pipelineRows = pipelines.data ?? []
  const hasLakehouseEvidence =
    serviceRows.length > 0 ||
    operationRows.length > 0 ||
    assetRows.length > 0 ||
    qualityRows.length > 0 ||
    logRows.length > 0
  const fallbackAttentionItems = buildAttentionItems({
    services: serviceRows,
    operations: operationRows,
    quality: qualityRows,
    logs: logRows,
    enabled: capabilities?.data?.features,
  })
  const attentionItems =
    overview.data?.attention && overview.data.attention.length > 0
      ? normalizeOverviewRows(overview.data.attention, fallbackAttentionItems)
      : fallbackAttentionItems
  const mapModel = useMemo(
    () =>
      buildLakehouseMap({
        assets: assetRows,
        datasets: datasetRows,
        quality: qualityRows,
        operations: operationRows,
      }),
    [assetRows, datasetRows, qualityRows, operationRows],
  )
  const derivedHealth =
    overview.data?.health ??
    (hasLakehouseEvidence
      ? {
          message:
            attentionItems.length > 0
              ? `${attentionItems.length} items need attention`
              : 'All systems nominal',
          state:
            attentionItems.length > 0 ? ('warning' as const) : ('ok' as const),
        }
      : null)
  const apiError =
    services.error ??
    operations.error ??
    assets.error ??
    quality.error ??
    logs.error ??
    datasets.error ??
    pipelines.error ??
    (hasLakehouseEvidence ? null : overview.error)
  const statusLabel =
    derivedHealth?.message ??
    (apiError ? 'Lakehouse API unreachable' : 'Syncing lakehouse state')
  const statusState = derivedHealth?.state ?? (apiError ? 'error' : 'unknown')

  const search = useSearch({ strict: false })
  const navigate = useNavigate()
  const selectedId = search.node ?? null
  const setSelected = useCallback(
    (id: string | null) => {
      void navigate({
        replace: true,
        search: id ? { node: id } : {},
        to: '/',
      })
    },
    [navigate],
  )

  const selectedNode = mapModel.nodes.find((node) => node.id === selectedId)
  const selectedPipeline = selectedNode
    ? pipelineRows.find(
        (pipeline) => pipeline.dataset?.id === selectedNode.datasetId,
      )
    : undefined
  const runningCount = mapModel.nodes.filter(
    (node) => node.activity === 'running',
  ).length

  const refresh = () => {
    for (const field of [
      'assets',
      'datasets',
      'quality',
      'operations',
      'pipelines',
      'overview',
    ]) {
      void loadCachedResource(
        `observatory:${field}`,
        REFRESH_LOADERS[field] ??
          (() => Promise.resolve({ data: null, error: null })),
        { force: true, staleMs: 0 },
      ).then((next) => {
        setOverviewState({
          [field]: next,
          updatedAt: new Date(),
        } as Partial<OverviewState>)
      })
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Map command bar: state of the system + posture links. */}
      <div className="border-rule flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b px-4 py-2.5">
        <div className="flex items-center gap-2">
          <h1 className="text-ink text-sm font-semibold">Lakehouse map</h1>
          <StatusBadge label={statusLabel} state={statusState} />
        </div>
        <div className="text-ink-faint flex items-center gap-2 text-xs">
          <span>{formatter.format(mapModel.nodes.length)} nodes</span>
          <span aria-hidden="true">·</span>
          <span>{formatter.format(mapModel.edges.length)} edges</span>
          {runningCount > 0 && (
            <>
              <span aria-hidden="true">·</span>
              <span className="text-blue">{runningCount} running</span>
            </>
          )}
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-2">
          {updatedAt && (
            <span className="text-ink-faint font-mono text-[10px]">
              synced {formatRelativeTime(updatedAt.toISOString())}
            </span>
          )}
          <Link
            className="border-rule text-ink-soft hover:border-foreground/25 hover:text-ink inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors"
            to="/now"
          >
            Triage
            {attentionItems.length > 0 && (
              <span className="bg-status-band-warning text-amber-ink rounded-full px-1.5 text-[10px] font-semibold">
                {attentionItems.length}
              </span>
            )}
          </Link>
          <Link
            className="border-rule text-ink-soft hover:border-foreground/25 hover:text-ink inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors"
            to="/pulse"
          >
            Pulse
          </Link>
        </div>
      </div>

      {/* Attention strip: ranked triage chips, one line, horizontally scrolled. */}
      {attentionItems.length > 0 && (
        <div className="border-rule scrollbar-thin flex flex-none gap-2 overflow-x-auto border-b px-4 py-2">
          {attentionItems.map((item) => (
            <Link
              className={cn(
                'flex h-8 flex-none items-center gap-2 rounded-full border px-3 text-xs whitespace-nowrap transition-colors',
                attentionChipClass(statusStateFor(item.state)),
              )}
              key={item.id}
              title={item.reason ?? undefined}
              to={item.href}
            >
              <HealthDot state={statusStateFor(item.state)} />
              <span className="text-[10px] font-medium tracking-wide uppercase opacity-70">
                {item.kind}
              </span>
              <span className="max-w-56 truncate font-medium">
                {item.label}
              </span>
            </Link>
          ))}
        </div>
      )}

      {/* The map + selection dock. */}
      <div className="relative flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          <LakehouseMap
            model={mapModel}
            onSelect={(id) => setSelected(id === selectedId ? null : id)}
            selectedId={selectedId}
          />
        </div>
        {selectedNode && (
          <NodeDock
            node={selectedNode}
            onClose={() => setSelected(null)}
            onMutated={refresh}
            pipeline={selectedPipeline ?? null}
          />
        )}
      </div>
    </div>
  )
}

const REFRESH_LOADERS: Record<
  string,
  () => Promise<ObservatoryResourceResult<unknown>>
> = {
  assets: getObservatoryAssetRecords,
  datasets: getObservatoryDatasetRecords,
  operations: getObservatoryOperationRecords,
  overview: getObservatoryOverview,
  pipelines: getObservatoryPipelineRecords,
  quality: getObservatoryQualityRecords,
}

function attentionChipClass(state: string): string {
  switch (state) {
    case 'error':
      return 'border-print-red/30 bg-status-band-error text-print-red'
    case 'warning':
      return 'border-amber-ink/30 bg-status-band-warning text-amber-ink'
    case 'ok':
      return 'border-ok-ink/30 bg-status-band-ok text-ok-ink'
    default:
      return 'border-rule bg-raised text-ink-soft'
  }
}

export function buildEventStory(
  operations: Array<ObservatoryOperation>,
  logs: Array<ObservatoryLogEvent>,
) {
  const operationEvents = operations.map((operation) => ({
    id: `operation:${operation.id}`,
    href: `/operations?operationId=${encodeURIComponent(operation.id)}`,
    label: operation.name,
    kind: 'operation',
    meta: [
      operation.kind,
      operation.target?.label,
      operation.completed_at ?? operation.started_at,
    ]
      .filter(Boolean)
      .join(' · '),
    state: operation.health.state,
    reason:
      operation.status === 'failed'
        ? eventReason(
            failureReason(operation) ?? 'Run failed.',
            operation.target?.label ?? operation.kind,
            'Open run evidence and recovery context.',
          )
        : eventReason(
            operation.health.message ?? 'Run completed.',
            operation.target?.label ?? operation.kind,
            'Open run evidence.',
          ),
    sort: operation.completed_at ?? operation.started_at ?? '',
    score: scoreOperation(operation),
  }))
  const logEvents = logs.filter(isFrontPageLog).map((log) => ({
    id: `log:${log.id}`,
    href: `/logs?logId=${encodeURIComponent(log.id)}`,
    label: log.message,
    kind: 'log',
    meta: [
      displayLogSource(log.source),
      log.level,
      log.resource?.label,
      log.timestamp,
    ]
      .filter(Boolean)
      .join(' · '),
    reason: eventReason(
      log.message,
      log.resource?.label ?? 'platform event',
      'Open structured log evidence.',
    ),
    state:
      log.level === 'error'
        ? 'error'
        : log.level === 'warning'
          ? 'warning'
          : 'ok',
    sort: log.timestamp ?? '',
    score: scoreLog(log),
  }))
  return {
    events: [...operationEvents, ...logEvents]
      .sort((left, right) => {
        if (left.score !== right.score) return right.score - left.score
        return right.sort.localeCompare(left.sort)
      })
      .slice(0, 6),
  }
}

function scoreOperation(operation: ObservatoryOperation): number {
  let score = 20
  if (operation.kind.startsWith('pipeline')) score += 50
  if (operation.kind.includes('quality')) score += 35
  if (operation.status === 'failed') score += 45
  if (operation.status === 'succeeded') score += 10
  if (
    operation.target?.kind === 'asset' ||
    operation.target?.kind === 'table'
  ) {
    score += 15
  }
  return score
}

function scoreLog(log: ObservatoryLogEvent): number {
  let score = 5
  if (log.level === 'error') score += 40
  if (log.level === 'warning') score += 20
  if (
    log.resource?.kind === 'asset' ||
    log.resource?.kind === 'dataset' ||
    log.resource?.kind === 'table'
  ) {
    score += 20
  }
  if (log.source?.toLowerCase().includes('keystone')) score += 20
  return score
}

function isFrontPageLog(log: ObservatoryLogEvent): boolean {
  return !isNoisyLog(log) && Boolean(log.resource)
}

export function isNoisyLog(log: ObservatoryLogEvent): boolean {
  const message = log.message.toLowerCase()
  const source = log.source?.toLowerCase() ?? ''
  const event = String(log.metadata?.event ?? '').toLowerCase()
  return [
    'failed_to_discover_user_workflows',
    'hasura_using_generated_default_admin_secret',
    'no heartbeat received',
    'optional_capability_degraded',
    'unknown_plugin_type',
    'plugin_load_failed',
    'plugin_registry_fetch_fallback',
    'observatory_settings_falling_back_to_memory',
    'using the generated default hasura admin secret',
    'workflows directory not found',
  ].some(
    (needle) =>
      message.includes(needle) ||
      source.includes(needle) ||
      event.includes(needle),
  )
}

function failureReason(operation: ObservatoryOperation): string | undefined {
  return (
    firstTextMetric(operation.metadata, [
      'exception_message',
      'failure_reason',
      'error',
      'reason',
      'message',
    ]) ??
    operation.health.message ??
    undefined
  )
}

function firstTextMetric(
  metadata: Record<string, NonNullable<unknown>>,
  keys: Array<string>,
): string | undefined {
  for (const key of keys) {
    const value = metadata[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return undefined
}

export function buildAttentionItems({
  services,
  operations,
  quality,
  logs,
  enabled,
}: {
  services: Array<ObservatoryService>
  operations: Array<ObservatoryOperation>
  quality: Array<ObservatoryQualityCheck>
  logs: Array<ObservatoryLogEvent>
  enabled?: Record<string, boolean>
}) {
  const qualityResourceIds = new Set(
    quality
      .filter((check) => check.status !== 'passing')
      .map((check) => check.asset_id),
  )
  const failedOperationResourceIds = new Set(
    operations
      .filter((operation) => operation.status === 'failed')
      .map((operation) => operation.target?.id)
      .filter(Boolean),
  )

  return [
    ...services
      .filter(serviceNeedsAttention)
      .slice(0, 3)
      .map((service) => ({
        id: `service:${service.id}`,
        href: `/services?serviceId=${encodeURIComponent(service.id)}`,
        kind: 'service',
        label: service.name,
        meta: [
          service.health.message ?? service.status,
          'owner: platform',
        ].join(' · '),
        reason: serviceActionHint(service),
        state: service.health.state,
      })),
    ...(enabled?.quality === false
      ? []
      : quality
          .filter((check) => check.status !== 'passing')
          .sort(compareAttentionChecks)
          .slice(0, 3)
          .map((check) => ({
            id: `quality:${check.id}`,
            href: `/quality?checkId=${encodeURIComponent(check.id)}`,
            kind: 'quality',
            label: check.name,
            meta: [
              `scope: ${qualityScopeLabel(check)}`,
              `owner: ${readQualityMetadata(check, 'owner') ?? 'unassigned'}`,
              check.severity ?? check.status,
            ].join(' · '),
            reason: qualityAttentionReason(check),
            state: check.status === 'failing' ? 'error' : 'warning',
          }))),
    ...(enabled?.operations === false
      ? []
      : operations
          .filter((operation) => operation.status === 'failed')
          .slice(0, 2)
          .map((operation) => ({
            id: `operation:${operation.id}`,
            href: `/operations?operationId=${encodeURIComponent(operation.id)}`,
            kind: 'operation',
            label: operation.name,
            meta: [
              failureReason(operation) ?? 'Run failed',
              `scope: ${operation.target?.label ?? operation.kind}`,
            ].join(' · '),
            reason: 'Open run evidence and recovery context.',
            state: 'error',
          }))),
    ...(enabled?.logs === false
      ? []
      : logs
          .filter(
            (log) =>
              log.level === 'error' &&
              isFrontPageLog(log) &&
              !qualityResourceIds.has(log.resource?.id ?? '') &&
              !failedOperationResourceIds.has(log.resource?.id ?? ''),
          )
          .slice(0, 2)
          .map((log) => ({
            id: `log:${log.id}`,
            href: `/logs?logId=${encodeURIComponent(log.id)}`,
            kind: 'log',
            label: log.message,
            meta: [
              displayLogSource(log.source),
              `scope: ${log.resource?.label ?? 'platform event'}`,
              log.timestamp,
            ]
              .filter(Boolean)
              .join(' · '),
            reason: log.resource
              ? `Open structured evidence for ${log.resource.label}.`
              : 'Open structured log evidence.',
            state: 'error',
          }))),
  ]
}

function normalizeOverviewRows(
  rows: Array<ObservatoryOverviewRow>,
  localRows: Array<{
    id: string
    href: string
    meta?: string | null
    reason?: string | null
  }>,
): Array<ObservatoryOverviewRow> {
  const localById = new Map(localRows.map((row) => [row.id, row]))
  return rows.map((row) => {
    const local = localById.get(row.id)
    return {
      ...row,
      href: local?.href ?? exactOverviewHref(row),
      meta: cleanOverviewMeta(row.meta) ?? local?.meta ?? null,
      reason: local?.reason ?? row.reason ?? overviewRowNextStep(row),
    }
  })
}

function exactOverviewHref(row: ObservatoryOverviewRow): string {
  const prefix = `${row.kind}:`
  const rawId = row.id.startsWith(prefix) ? row.id.slice(prefix.length) : row.id
  const encodedId = encodeURIComponent(rawId ?? row.id)
  if (row.kind === 'quality') return `/quality?checkId=${encodedId}`
  if (row.kind === 'operation') return `/operations?operationId=${encodedId}`
  if (row.kind === 'log') return `/logs?logId=${encodedId}`
  if (row.kind === 'service') return `/services?serviceId=${encodedId}`
  return row.href
}

function cleanOverviewMeta(value?: string | null): string | null {
  if (!value) return value ?? null
  return value
    .replace(/\bassets?\b/gi, 'resources')
    .replace(/\bnodes?\b/gi, 'resources')
    .replace(/\bissues?\b/gi, 'checks')
}

function displayLogSource(source?: string | null): string | null {
  if (!source) return source ?? null
  if (source === 'observatory-fixture') return 'manifest evidence'
  return source.replace(/\bassets\b/gi, 'resources')
}

function overviewRowNextStep(row: ObservatoryOverviewRow): string {
  if (row.kind === 'quality') {
    return 'Open triage with impact, evidence, owner, and next action.'
  }
  if (row.kind === 'operation') {
    return 'Open run evidence and recovery context.'
  }
  if (row.kind === 'log') return 'Open structured log evidence.'
  return 'Open service detail and available recovery context.'
}

function eventReason(
  problem: string,
  scope: string,
  nextAction: string,
): string {
  return `${problem} Scope: ${scope}. Next: ${nextAction}`
}

function qualityScopeLabel(check: ObservatoryQualityCheck): string {
  return readQualityMetadata(check, 'dataset') ?? check.asset_id
}

function readQualityMetadata(
  check: ObservatoryQualityCheck,
  key: string,
): string | null {
  const value = check.metadata?.[key]
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

function compareAttentionChecks(
  left: ObservatoryQualityCheck,
  right: ObservatoryQualityCheck,
): number {
  const leftScore = qualityAttentionScore(left)
  const rightScore = qualityAttentionScore(right)
  if (leftScore !== rightScore) return leftScore - rightScore
  return left.id.localeCompare(right.id)
}

function qualityAttentionScore(check: ObservatoryQualityCheck): number {
  const stateScore =
    check.status === 'failing' ? 0 : check.status === 'warning' ? 10 : 20
  const severityScore =
    check.severity === 'critical'
      ? 0
      : check.severity === 'high'
        ? 1
        : check.severity === 'medium'
          ? 2
          : check.severity === 'low'
            ? 3
            : 4
  return stateScore + severityScore
}

function serviceActionHint(service: ObservatoryService): string {
  if (service.status === 'unhealthy') return 'Inspect service health and logs.'
  if (service.status === 'stopped')
    return 'Inspect service state and available recovery actions.'
  if (service.health.state === 'warning')
    return 'Review degraded service evidence.'
  if (service.health.state === 'error')
    return 'Open service detail and recovery actions.'
  return 'Open service detail.'
}

function qualityAttentionReason(check: ObservatoryQualityCheck): string {
  if (check.status === 'failing' && check.blocking) {
    return 'Open triage with impact, run evidence, logs, and next action.'
  }
  if (check.status === 'warning') {
    return 'Open triage and decide whether this blocks release.'
  }
  if (check.status === 'unknown') {
    return 'Open triage and collect fresh quality evidence.'
  }
  return 'Open quality evidence.'
}

function serviceNeedsAttention(service: ObservatoryService): boolean {
  if (!isConfiguredService(service)) return false
  if (service.health.state === 'error' || service.health.state === 'warning') {
    return true
  }
  if (service.status === 'unhealthy') return true
  return service.status === 'stopped' && service.health.state !== 'ok'
}

export function isBlockingQualityIssue(
  check: ObservatoryQualityCheck,
): boolean {
  return check.blocking && check.status !== 'passing'
}

function isConfiguredService(service: ObservatoryService): boolean {
  if (typeof service.in_stack === 'boolean') return service.in_stack
  return service.definition_state === 'configured'
}
