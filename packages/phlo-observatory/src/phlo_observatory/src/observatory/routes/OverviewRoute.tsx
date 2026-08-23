/**
 * Overview dashboard shared by the index route. Aggregates overview,
 * assets, branches, services, operations, quality, and log data into one
 * reducer-driven snapshot; loadOverviewSnapshotFromApi lets a route loader
 * pre-fetch the full state.
 */
import {
  Activity,
  AlertCircle,
  Boxes,
  Database,
  GitBranch,
  GitCommitHorizontal,
  ListChecks,
  Server,
  Workflow,
} from 'lucide-react'
import { Link } from '@tanstack/react-router'
import { useEffect, useMemo, useReducer } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryAsset,
  ObservatoryBranch,
  ObservatoryCapabilities,
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
  getObservatoryBranchRecords,
  getObservatoryCapabilities,
  getObservatoryLogRecords,
  getObservatoryOperationRecords,
  getObservatoryOverview,
  getObservatoryQualityRecords,
  getObservatoryServices,
} from '@/observatory/api/resources'
import { StatusBadge } from '@/observatory/components/StatusBadge'
import { loadCachedResource } from '@/observatory/routes/liveResource'

const formatter = new Intl.NumberFormat('en')
const refreshTimeFormatter = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  hour12: false,
  minute: '2-digit',
  second: '2-digit',
  timeZone: 'UTC',
})
const emptyResult = { data: null, error: null }
const stageTransitions = ['ingest', 'normalize', 'model', 'publish']

type OverviewState = {
  assets: ObservatoryResourceResult<Array<ObservatoryAsset>>
  branches: ObservatoryResourceResult<Array<ObservatoryBranch>>
  capabilities: ObservatoryResourceResult<ObservatoryCapabilities> | null
  logs: ObservatoryResourceResult<Array<ObservatoryLogEvent>>
  operations: ObservatoryResourceResult<Array<ObservatoryOperation>>
  overview: ObservatoryResourceResult<ObservatoryOverview>
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
    branches: empty,
    capabilities: null,
    logs: empty,
    operations: empty,
    overview: pending,
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
    branches,
    capabilities,
  ] = await Promise.all([
    getObservatoryOverview(),
    getObservatoryServices(),
    getObservatoryOperationRecords(),
    getObservatoryAssetRecords(),
    getObservatoryQualityRecords(),
    getObservatoryLogRecords(),
    getObservatoryBranchRecords(),
    getObservatoryCapabilities(),
  ])

  return {
    assets,
    branches,
    capabilities,
    logs,
    operations,
    overview,
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
      branches,
      capabilities,
      logs,
      operations,
      overview,
      quality,
      services,
      updatedAt,
    },
    setOverviewState,
  ] = useReducer(overviewReducer, {
    assets: initialSnapshot?.assets ?? emptyResult,
    branches: initialSnapshot?.branches ?? emptyResult,
    capabilities: initialSnapshot?.capabilities ?? null,
    logs: initialSnapshot?.logs ?? emptyResult,
    operations: initialSnapshot?.operations ?? emptyResult,
    overview: initialSnapshot?.overview ?? emptyResult,
    quality: initialSnapshot?.quality ?? emptyResult,
    services: initialSnapshot?.services ?? emptyResult,
    updatedAt: initialSnapshot?.updatedAt
      ? new Date(initialSnapshot.updatedAt)
      : null,
  })

  useEffect(() => {
    let cancelled = false

    function load(force = false) {
      loadCachedResource('observatory:services', getObservatoryServices, {
        force,
        staleMs: 60_000,
      }).then((nextServices) => {
        if (!cancelled) {
          setOverviewState({ services: nextServices, updatedAt: new Date() })
        }
      })

      loadCachedResource(
        'observatory:operations',
        getObservatoryOperationRecords,
        {
          force,
          staleMs: 60_000,
        },
      ).then((nextOperations) => {
        if (!cancelled) {
          setOverviewState({
            operations: nextOperations,
            updatedAt: new Date(),
          })
        }
      })

      loadCachedResource('observatory:assets', getObservatoryAssetRecords, {
        force,
        staleMs: 60_000,
      }).then((nextAssets) => {
        if (!cancelled) {
          setOverviewState({ assets: nextAssets, updatedAt: new Date() })
        }
      })

      loadCachedResource('observatory:quality', getObservatoryQualityRecords, {
        force,
        staleMs: 60_000,
      }).then((nextQuality) => {
        if (!cancelled) {
          setOverviewState({ quality: nextQuality, updatedAt: new Date() })
        }
      })

      loadCachedResource('observatory:logs', getObservatoryLogRecords, {
        force,
        staleMs: 30_000,
      }).then((nextLogs) => {
        if (!cancelled) {
          setOverviewState({ logs: nextLogs, updatedAt: new Date() })
        }
      })

      loadCachedResource('observatory:branches', getObservatoryBranchRecords, {
        force,
        staleMs: 60_000,
      }).then((nextBranches) => {
        if (!cancelled) {
          setOverviewState({ branches: nextBranches, updatedAt: new Date() })
        }
      })

      loadCachedResource(
        'observatory:capabilities',
        getObservatoryCapabilities,
        {
          force,
          staleMs: 120_000,
        },
      ).then((nextCapabilities) => {
        if (!cancelled) {
          setOverviewState({
            capabilities: nextCapabilities,
            updatedAt: new Date(),
          })
        }
      })

      loadCachedResource('observatory:overview', getObservatoryOverview, {
        force,
        staleMs: 30_000,
      }).then((nextOverview) => {
        if (!cancelled) {
          setOverviewState({ overview: nextOverview, updatedAt: new Date() })
        }
      })
    }

    load(true)
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') {
        load(true)
      }
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
  const branchRows = branches.data ?? []
  const counters = overview.data?.counters ?? {}
  const runningServices = useMemo(
    () =>
      serviceRows.filter(
        (service) =>
          isConfiguredService(service) && service.status === 'running',
      ).length,
    [serviceRows],
  )
  const configuredServices = useMemo(
    () => serviceRows.filter(isConfiguredService).length,
    [serviceRows],
  )
  const attentionServices = useMemo(
    () => serviceRows.filter(serviceNeedsAttention).length,
    [serviceRows],
  )
  const blockingChecks = qualityRows.filter(isBlockingQualityIssue).length
  const failedOperations = operationRows.filter(
    (operation) => operation.status === 'failed',
  ).length
  const activeBranches = branchRows.filter((branch) => !branch.current).length
  const errorLogs = logRows.filter((log) => log.level === 'error').length
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
  const lakehouseStages = useMemo(
    () => buildLakehouseStages(assetRows, qualityRows),
    [assetRows, qualityRows],
  )
  const fallbackEventStory = useMemo(
    () => buildEventStory(operationRows, logRows),
    [logRows, operationRows],
  )
  const eventRows =
    overview.data?.events && overview.data.events.length > 0
      ? normalizeOverviewRows(overview.data.events, fallbackEventStory.events)
      : fallbackEventStory.events
  const integrationLinks = useMemo(
    () => buildIntegrationLinks(serviceRows),
    [serviceRows],
  )
  const recentEvidence = logRows.filter((log) => !isNoisyLog(log)).slice(0, 4)
  const derivedHealth =
    overview.data?.health ??
    (hasLakehouseEvidence
      ? {
          message:
            attentionItems.length > 0
              ? `${attentionItems.length} items need attention`
              : 'Lakehouse snapshot ready',
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
    branches.error ??
    (hasLakehouseEvidence ? null : overview.error)
  const statusLabel =
    derivedHealth?.message ??
    (apiError ? 'API needs attention' : 'Loading lakehouse snapshot')
  const statusState =
    derivedHealth?.state ??
    (apiError ? ('warning' as const) : ('unknown' as const))

  return (
    <div className="phlo-observatory-content">
      <header className="phlo-observatory-section-header">
        <div>
          <div className="phlo-observatory-kicker">Home</div>
          <h1 className="phlo-observatory-title">Lakehouse control</h1>
          <p className="phlo-observatory-subtitle">
            The cross-domain queue: what needs attention, why it matters, and
            where to move next.
          </p>
        </div>
        <StatusBadge label={statusLabel} state={statusState} />
      </header>

      <section className="phlo-observatory-grid" aria-label="Platform counters">
        <MetricTile
          icon={<Server className="size-4" />}
          label="Services"
          note={`${formatter.format(configuredServices)} configured`}
          value={formatter.format(runningServices)}
        />
        <MetricTile
          icon={<AlertCircle className="size-4" />}
          label="Attention"
          note="Services, checks, operations, logs"
          value={formatter.format(attentionItems.length)}
        />
        <MetricTile
          icon={<Boxes className="size-4" />}
          label="Operational scope"
          note="Datasets and lineage"
          value={counterValue(counters.assets, assetRows.length)}
        />
        {featureEnabled(capabilities?.data, 'branches') && (
          <MetricTile
            icon={<GitBranch className="size-4" />}
            label="Change Risk"
            note="Non-current lakehouse branches"
            value={formatter.format(activeBranches)}
          />
        )}
      </section>

      <section
        className="phlo-observatory-lakehouse-map"
        aria-label="Lakehouse map"
      >
        <div className="phlo-observatory-map-header">
          <h2>Lakehouse map</h2>
          <Link className="phlo-observatory-map-action" to="/workflows/new">
            <Workflow className="size-4" />
            Build workflow
          </Link>
        </div>
        <div className="phlo-observatory-flow-stage-map">
          {lakehouseStages.map((stage, index) => (
            <Link
              className="phlo-observatory-stage-card"
              data-state={stage.state}
              data-transition={stageTransitions[index] ?? ''}
              key={stage.id}
              to={stage.href}
            >
              <div className="phlo-observatory-stage-card-top">
                <span>{stage.label}</span>
                <StatusBadge label={stage.state} state={stage.state} />
              </div>
              <div className="phlo-observatory-stage-body">
                <div className="phlo-observatory-stage-primary">
                  <strong>{formatter.format(stage.records)}</strong>
                  <span>records</span>
                </div>
                <small>
                  {stage.assets} mapped dependencies · {stage.datasets} Datasets
                  · {stage.blocking} checks
                </small>
              </div>
              <div className="phlo-observatory-stage-samples">
                {stage.samples.length > 0 ? (
                  stage.samples.map((sample) => (
                    <span key={`${stage.id}:${sample}`}>{sample}</span>
                  ))
                ) : (
                  <span>No Datasets in this stage</span>
                )}
              </div>
              <div className="phlo-observatory-stage-meter">
                <span style={{ width: `${stage.weight}%` }} />
              </div>
            </Link>
          ))}
        </div>
        <div className="phlo-observatory-evidence-grid">
          <div className="phlo-observatory-evidence-panel">
            <div className="phlo-observatory-panel-header">
              <h2 className="phlo-observatory-panel-title">Event story</h2>
              <span className="phlo-observatory-pill">
                <GitCommitHorizontal className="size-3.5" />
                {eventRows.length > 0
                  ? `${eventRows.length} relevant`
                  : 'Empty'}
              </span>
            </div>
            <div className="phlo-observatory-list">
              {eventRows.length > 0 ? (
                eventRows.map((event) => (
                  <Link
                    className="phlo-observatory-row"
                    data-state={event.state}
                    key={event.id}
                    to={event.href}
                  >
                    <div className="phlo-observatory-row-main">
                      <div className="phlo-observatory-row-title">
                        <span
                          className="phlo-observatory-dot"
                          data-state={event.state}
                        />
                        <span>{event.label}</span>
                      </div>
                      <div className="phlo-observatory-row-meta">
                        {event.meta}
                      </div>
                      {event.reason && (
                        <div className="phlo-observatory-row-evidence">
                          {event.reason}
                        </div>
                      )}
                    </div>
                    <span className="phlo-observatory-pill">{event.kind}</span>
                  </Link>
                ))
              ) : (
                <EmptyRow label="No events yet" />
              )}
            </div>
          </div>
          <div className="phlo-observatory-evidence-panel">
            <div className="phlo-observatory-panel-header">
              <h2 className="phlo-observatory-panel-title">
                Native workbenches
              </h2>
              <span className="phlo-observatory-pill">
                <Database className="size-3.5" />
                {integrationLinks.length || 'None'}
              </span>
            </div>
            <div className="phlo-observatory-integration-grid">
              {integrationLinks.length > 0 ? (
                integrationLinks.map((link) => (
                  <a
                    className="phlo-observatory-integration-link"
                    data-state={link.status}
                    href={link.url}
                    key={`${link.service}:${link.label}:${link.url}`}
                    rel="noreferrer"
                    target="_blank"
                  >
                    <span className="phlo-observatory-integration-mark">
                      {link.initials}
                    </span>
                    <span className="phlo-observatory-integration-copy">
                      <strong>{link.label}</strong>
                      <span className="phlo-observatory-integration-meta">
                        <span>{link.service}</span>
                        <span>{link.description}</span>
                      </span>
                      <code>{link.host}</code>
                    </span>
                    <span className="phlo-observatory-integration-status">
                      {link.status}
                    </span>
                  </a>
                ))
              ) : (
                <EmptyRow label="No native links available" />
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="phlo-observatory-command">
        <div className="phlo-observatory-command-primary">
          <div className="phlo-observatory-panel">
            <div className="phlo-observatory-panel-header">
              <h2 className="phlo-observatory-panel-title">Attention queue</h2>
              <span className="phlo-observatory-pill">
                <Activity className="size-3.5" />
                {attentionItems.length || 'Clear'}
              </span>
            </div>
            <div className="phlo-observatory-list">
              {attentionItems.length > 0 ? (
                attentionItems.map((item) => (
                  <Link
                    className="phlo-observatory-row"
                    data-state={item.state}
                    key={item.id}
                    to={item.href}
                  >
                    <div className="phlo-observatory-row-main">
                      <div className="phlo-observatory-row-title">
                        <span
                          className="phlo-observatory-dot"
                          data-state={item.state}
                        />
                        <span>{item.label}</span>
                      </div>
                      <div className="phlo-observatory-row-meta">
                        {item.meta}
                      </div>
                      {item.reason && (
                        <div className="phlo-observatory-row-evidence">
                          {item.reason}
                        </div>
                      )}
                    </div>
                    <span className="phlo-observatory-pill">{item.kind}</span>
                  </Link>
                ))
              ) : (
                <EmptyRow label="No active attention items" />
              )}
            </div>
          </div>

          <section className="phlo-observatory-diff-metrics">
            {(featureEnabled(capabilities?.data, 'quality') ||
              qualityRows.length > 0) && (
              <CommandTile
                href="/quality"
                icon={<ListChecks className="size-5" />}
                label="Triage checks"
                value={`${blockingChecks} blocking`}
              />
            )}
            {featureEnabled(capabilities?.data, 'operations') && (
              <CommandTile
                href="/operations"
                icon={<Activity className="size-5" />}
                label="Review actions"
                value={`${failedOperations} failed`}
              />
            )}
            <CommandTile
              href="/lineage"
              icon={<Boxes className="size-5" />}
              label="Inspect impact"
              value={`${assetRows.length} mapped dependencies`}
            />
            {featureEnabled(capabilities?.data, 'branches') && (
              <CommandTile
                href="/branches"
                icon={<GitBranch className="size-5" />}
                label="Check changes"
                value={`${activeBranches} active`}
              />
            )}
          </section>
        </div>

        <aside className="phlo-observatory-inspector">
          <div className="phlo-observatory-inspector-label">
            Control context
          </div>
          <h2>{statusLabel}</h2>
          <p>
            Last refreshed{' '}
            {updatedAt
              ? refreshTimeFormatter.format(updatedAt)
              : 'after first load'}
            .
          </p>
          <dl className="phlo-observatory-facts">
            <Fact
              label="Services needing attention"
              value={attentionServices}
            />
            <Fact label="Blocking checks" value={blockingChecks} />
            <Fact label="Failed operations" value={failedOperations} />
            <Fact label="Error logs" value={errorLogs} />
          </dl>
          <div className="phlo-observatory-detail-list">
            {recentEvidence.length > 0 ? (
              recentEvidence.map((log) => (
                <div className="phlo-observatory-mini-row" key={log.id}>
                  <span>{log.message}</span>
                  <small>
                    {[log.level, log.source, log.timestamp]
                      .filter(Boolean)
                      .join(' · ')}
                  </small>
                </div>
              ))
            ) : (
              <div className="phlo-observatory-mini-row">
                <span>No recent evidence</span>
                <small>
                  Logs will appear as Phlo and stack services emit events.
                </small>
              </div>
            )}
          </div>
          {apiError && (
            <div className="phlo-observatory-panel-footer">{apiError}</div>
          )}
        </aside>
      </section>
    </div>
  )
}

function MetricTile({
  icon,
  label,
  note,
  value,
}: {
  icon: ReactNode
  label: string
  note: string
  value: string
}) {
  return (
    <div className="phlo-observatory-tile">
      <div className="phlo-observatory-tile-label">
        <span>{label}</span>
        {icon}
      </div>
      <div className="phlo-observatory-tile-value">{value}</div>
      <div className="phlo-observatory-tile-note">{note}</div>
    </div>
  )
}

function CommandTile({
  href,
  icon,
  label,
  value,
}: {
  href: string
  icon: ReactNode
  label: string
  value: string
}) {
  return (
    <Link className="phlo-observatory-diff-metric" to={href}>
      {icon}
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
      </div>
    </Link>
  )
}

function Fact({ label, value }: { label: string; value: string | number }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{String(value)}</dd>
    </>
  )
}

function EmptyRow({ label }: { label: string }) {
  return (
    <div className="phlo-observatory-row">
      <div className="phlo-observatory-row-main">
        <div className="phlo-observatory-row-title">{label}</div>
        <div className="phlo-observatory-row-meta">
          Connect a running lakehouse or add Datasets to populate this surface.
        </div>
      </div>
    </div>
  )
}

function buildLakehouseStages(
  assets: Array<ObservatoryAsset>,
  quality: Array<ObservatoryQualityCheck>,
) {
  const stageOrder = ['source', 'bronze', 'silver', 'gold', 'serving']
  const stages = new Map(
    stageOrder.map((stage) => [
      stage,
      {
        id: stage,
        label: stageLabel(stage),
        assets: 0,
        datasets: 0,
        records: 0,
        blocking: 0,
        state: 'unknown' as 'ok' | 'warning' | 'error' | 'unknown',
        href: stage === 'serving' ? '/apis' : '/datasets',
        weight: 8,
        samples: [] as Array<string>,
      },
    ]),
  )
  const qualityByAsset = new Map<string, Array<ObservatoryQualityCheck>>()
  for (const check of quality) {
    const checks = qualityByAsset.get(check.asset_id)
    if (checks) {
      checks.push(check)
    } else {
      qualityByAsset.set(check.asset_id, [check])
    }
  }

  for (const asset of assets) {
    const stageId = inferStage(asset)
    const stage =
      stages.get(stageId) ??
      stages.get(stageId.replace('analytics', 'gold')) ??
      stages.get('gold')
    if (!stage) continue

    const records = readNumber(asset.metadata.records)
    const tables = asset.kinds.some((kind) =>
      ['table', 'dataset', 'analytics'].includes(kind),
    )
      ? 1
      : 0
    const checks = qualityByAsset.get(asset.id) ?? []
    const hasFailingCheck = checks.some((check) => check.status === 'failing')
    const hasWarningCheck = checks.some((check) => check.status === 'warning')
    const blockingChecks = checks.filter(isBlockingQualityIssue).length
    stage.assets += 1
    stage.datasets += tables
    stage.records += records
    stage.blocking += blockingChecks
    if (stage.samples.length < 3) stage.samples.push(asset.name)
    if (hasFailingCheck) stage.state = 'error'
    else if (hasWarningCheck && stage.state !== 'error') {
      stage.state = 'warning'
    } else if (stage.state === 'unknown') {
      stage.state = 'ok'
    }
  }

  const maxRecords = Math.max(
    1,
    ...Array.from(stages.values()).map((stage) => stage.records),
  )
  return Array.from(stages.values()).map((stage) => ({
    ...stage,
    weight: Math.max(8, Math.round((stage.records / maxRecords) * 100)),
  }))
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

function isNoisyLog(log: ObservatoryLogEvent): boolean {
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

function buildIntegrationLinks(services: Array<ObservatoryService>) {
  const browserWorkbenches = new Map<
    string,
    {
      label: string
      path?: string
      preferredPortLabel?: string
      preferredLink?: 'first' | 'last'
      requiresRunning?: boolean
    }
  >([
    ['observatory', { label: 'Observatory', requiresRunning: true }],
    [
      'hasura',
      { label: 'Hasura console', path: '/console', requiresRunning: true },
    ],
    ['phlo-api', { label: 'API docs', path: '/docs', requiresRunning: true }],
    ['dagster', { label: 'Dagster UI', requiresRunning: true }],
    ['grafana', { label: 'Grafana', requiresRunning: true }],
    ['superset', { label: 'Superset', requiresRunning: true }],
    [
      'minio',
      {
        label: 'Object browser',
        preferredLink: 'last',
        requiresRunning: true,
      },
    ],
    ['pgweb', { label: 'Postgres browser', requiresRunning: true }],
    ['openmetadata', { label: 'OpenMetadata', requiresRunning: true }],
    ['trino', { label: 'Trino UI', requiresRunning: true }],
  ])

  return services
    .flatMap((service) => {
      const workbench = browserWorkbenches.get(service.id)
      if (!workbench) return []
      if (workbench.requiresRunning && service.status !== 'running') return []
      const firstLink = chooseWorkbenchLink(
        service.links,
        workbench.preferredPortLabel,
        workbench.preferredLink,
      )
      if (!firstLink?.url) return []
      return [
        {
          service: service.name,
          label: workbench.label,
          status: service.status,
          url: withPath(firstLink.url, workbench.path),
          host: readableHost(firstLink.url),
          description: describeWorkbench(service.id),
          initials: serviceInitials(service.name),
        },
      ]
    })
    .slice(0, 6)
}

function chooseWorkbenchLink(
  links: Array<ObservatoryService['links'][number]>,
  preferredPortLabel?: string,
  preferredLink: 'first' | 'last' = 'first',
) {
  if (!links.length) return null

  const preferred = preferredPortLabel
    ? links.find((link) => link.label === preferredPortLabel)
    : null
  const projectLink = preferredLink === 'last' ? links.at(-1) : links[0]

  return preferred ?? projectLink ?? links[0]
}

function describeWorkbench(serviceId: string): string {
  const descriptions: Record<string, string> = {
    dagster: 'Pipeline runs and schedules',
    grafana: 'Metrics and service dashboards',
    hasura: 'Metadata graph and API console',
    minio: 'Lakehouse object storage',
    observatory: 'Current Phlo control plane',
    openmetadata: 'Catalog and ownership',
    'phlo-api': 'Phlo API contract and probes',
    pgweb: 'Postgres metadata browser',
    superset: 'Analytics workspace',
    trino: 'Distributed SQL console',
  }
  return descriptions[serviceId] ?? 'Native service workbench'
}

function readableHost(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url.replace(/^https?:\/\//, '')
  }
}

function serviceInitials(name: string): string {
  const words = name.replace(/[-_]/g, ' ').trim().split(/\s+/)
  if (words.length === 0 || !words[0]) return 'PH'
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return words
    .slice(0, 2)
    .map((word) => word[0])
    .join('')
    .toUpperCase()
}

function withPath(url: string, path?: string): string {
  if (!path) return url
  try {
    const parsed = new URL(url)
    parsed.pathname = path
    return parsed.toString()
  } catch {
    return url
  }
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

function inferStage(asset: ObservatoryAsset): string {
  const raw = [
    asset.group,
    asset.id,
    asset.name,
    asset.metadata.stage,
    asset.metadata.namespace,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  if (raw.includes('bronze') || raw.includes('raw')) return 'bronze'
  if (raw.includes('silver') || raw.includes('clean')) return 'silver'
  if (
    raw.includes('gold') ||
    raw.includes('analytics') ||
    raw.includes('mart')
  ) {
    return 'gold'
  }
  if (
    raw.includes('serving') ||
    raw.includes('api') ||
    raw.includes('publish')
  ) {
    return 'serving'
  }
  return raw.includes('source') || raw.includes('input') ? 'source' : 'gold'
}

function readNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function stageLabel(stage: string): string {
  return stage.charAt(0).toUpperCase() + stage.slice(1)
}

function counterValue(primary?: number, fallback?: number): string {
  const value = typeof fallback === 'number' ? fallback : primary
  return typeof value === 'number' ? formatter.format(value) : '--'
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

function featureEnabled(
  capabilities: ObservatoryCapabilities | null | undefined,
  key: string,
): boolean {
  if (!capabilities) return true
  return capabilities.features[key] !== false
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
