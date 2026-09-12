/**
 * Mission control overview shared by the index route. Aggregates overview,
 * assets, branches, services, operations, quality, and log data into one
 * reducer-driven snapshot; loadOverviewSnapshotFromApi lets a route loader
 * pre-fetch the full state.
 */
import {
  AlertCircle,
  Boxes,
  ExternalLink,
  GitBranch,
  GitCommitHorizontal,
  ListChecks,
  Server,
  Workflow,
} from 'lucide-react'
import { Link } from '@tanstack/react-router'
import { useEffect, useMemo, useReducer } from 'react'

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
import { loadCachedResource } from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import { RowItem, RowList } from '@/components/observatory/resource-list'
import { SectionCard } from '@/components/observatory/section'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import {
  HealthDot,
  StatusBadge,
  statusStateFor,
} from '@/components/observatory/status'
import { formatRelativeTime } from '@/components/observatory/time'
import { EmptyBlock } from '@/components/observatory/states'
import { cn } from '@/lib/utils'

const formatter = new Intl.NumberFormat('en')
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
    assets: initialSnapshot?.assets ?? { data: null, error: null },
    branches: initialSnapshot?.branches ?? { data: null, error: null },
    capabilities: initialSnapshot?.capabilities ?? null,
    logs: initialSnapshot?.logs ?? { data: null, error: null },
    operations: initialSnapshot?.operations ?? { data: null, error: null },
    overview: initialSnapshot?.overview ?? { data: null, error: null },
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
        ['branches', getObservatoryBranchRecords, 60_000],
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
    branches.error ??
    (hasLakehouseEvidence ? null : overview.error)
  const statusLabel =
    derivedHealth?.message ??
    (apiError ? 'Lakehouse API unreachable' : 'Syncing lakehouse state')
  const statusState = derivedHealth?.state ?? (apiError ? 'error' : 'unknown')

  return (
    <Page>
      <PageHeader
        actions={
          <div className="flex items-center gap-2">
            {updatedAt && (
              <span className="text-ink-faint font-mono text-[10px]">
                synced {formatRelativeTime(updatedAt.toISOString())}
              </span>
            )}
            <StatusBadge label={statusLabel} state={statusState} />
            <Link
              className={cn(
                'border-rule hover:bg-band inline-flex h-7 items-center gap-1.5 border px-2.5 font-mono text-[10px] font-bold tracking-[0.1em] uppercase',
              )}
              to="/workflows/new"
            >
              <Workflow className="size-3.5" />
              New workflow
            </Link>
          </div>
        }
        description="Lakehouse status report — what needs attention, why it matters, and where to go next."
        title="Overview"
      />

      {/* Figures line: the report's totals, ruled off as one strip. */}
      <StatGrid>
        <StatCard
          href="/services"
          icon={<Server className="size-3.5" />}
          label="Services"
          note={`${formatter.format(configuredServices)} in stack`}
          state={attentionServices > 0 ? 'warning' : 'ok'}
          value={`${formatter.format(runningServices)}`}
        />
        <StatCard
          icon={<AlertCircle className="size-3.5" />}
          label="Attention"
          note="Across services, checks, operations"
          state={attentionItems.length > 0 ? 'warning' : 'ok'}
          value={formatter.format(attentionItems.length)}
        />
        <StatCard
          href="/lineage"
          icon={<Boxes className="size-3.5" />}
          label="Assets"
          note="Mapped lineage resources"
          value={counterValue(counters.assets, assetRows.length)}
        />
        <StatCard
          href="/quality"
          icon={<ListChecks className="size-3.5" />}
          label="Blocking checks"
          note={`${formatter.format(qualityRows.length)} checks total`}
          state={blockingChecks > 0 ? 'error' : 'ok'}
          value={formatter.format(blockingChecks)}
        />
        <StatCard
          href="/operations"
          icon={<GitCommitHorizontal className="size-3.5" />}
          label="Failed ops"
          note={`${formatter.format(operationRows.length)} operations`}
          state={failedOperations > 0 ? 'error' : 'ok'}
          value={formatter.format(failedOperations)}
        />
        {featureEnabled(capabilities?.data, 'branches') && (
          <StatCard
            href="/branches"
            icon={<GitBranch className="size-3.5" />}
            label="Branches"
            note="Non-default change sets"
            state={activeBranches > 0 ? 'warning' : 'ok'}
            value={formatter.format(activeBranches)}
          />
        )}
      </StatGrid>

      {/* Stage track: the pipeline as a printed flow line, tinted by state. */}
      <SectionCard
        actions={
          <span className="text-ink-faint font-mono text-[9px] tracking-[0.14em] uppercase">
            {formatter.format(
              lakehouseStages.reduce((sum, stage) => sum + stage.assets, 0),
            )}{' '}
            assets
          </span>
        }
        title="Pipeline track"
      >
        <div className="border-rule scrollbar-thin flex items-stretch overflow-x-auto border">
          {lakehouseStages.map((stage, index) => (
            <Link
              className={cn(
                'group flex min-w-40 flex-1 flex-col gap-1.5 px-3 py-2',
                stageBandClass(stage.state),
                'hover:bg-band-strong',
              )}
              key={stage.id}
              to={stage.href}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-ink-soft font-mono text-[9px] font-bold tracking-[0.16em] uppercase">
                  {stageTransitions[index] ?? stage.id}
                </span>
                <HealthDot className="status-dot-sm" state={stage.state} />
              </div>
              <div className="text-ink text-xs font-bold">{stage.label}</div>
              <div className="text-ink-soft font-mono text-[10px]">
                {formatter.format(stage.records)} rec · {stage.assets} assets
              </div>
              <div className="text-ink-faint truncate font-mono text-[9px]">
                {stage.samples.length > 0
                  ? stage.samples.join(', ')
                  : 'no datasets mapped'}
              </div>
            </Link>
          ))}
        </div>
        <div className="text-ink-faint mt-1 flex justify-between font-mono text-[9px] tracking-[0.14em] uppercase">
          <span>source</span>
          <span>serving</span>
        </div>
      </SectionCard>

      {/* Attention queue: the sheet's main block of state-tinted bands. */}
      <SectionCard
        actions={
          <span className="text-ink-faint font-mono text-[9px] tracking-[0.14em] uppercase">
            {attentionItems.length || 'clear'}
          </span>
        }
        description="Ranked by severity: failing checks, failed work, degraded services, error logs."
        title="Attention queue"
      >
        {attentionItems.length > 0 ? (
          <RowList>
            {attentionItems.map((item) => (
              <RowItem
                badge={item.kind}
                href={item.href}
                key={item.id}
                meta={item.meta}
                reason={item.reason}
                state={statusStateFor(item.state)}
                title={item.label}
              />
            ))}
          </RowList>
        ) : (
          <EmptyBlock
            description="Services, checks, operations, and logs are all nominal."
            title="Nothing needs attention"
          />
        )}
      </SectionCard>

      {/* Event feed: the printout's running commentary. */}
      <SectionCard
        actions={
          <span className="text-ink-faint font-mono text-[9px] tracking-[0.14em] uppercase">
            {eventRows.length} lines
          </span>
        }
        description="Latest operations and platform events with evidence links."
        title="Event feed"
      >
        {eventRows.length > 0 ? (
          <RowList>
            {eventRows.map((event) => (
              <RowItem
                badge={event.kind}
                href={event.href}
                key={event.id}
                meta={event.meta}
                reason={event.reason}
                state={statusStateFor(event.state)}
                title={event.label}
              />
            ))}
          </RowList>
        ) : (
          <EmptyBlock title="No events yet" />
        )}
      </SectionCard>

      {/* Workbenches: external consoles printed as index lines. */}
      <SectionCard
        actions={
          <span className="text-ink-faint font-mono text-[9px] tracking-[0.14em] uppercase">
            {integrationLinks.length} consoles
          </span>
        }
        description="Native consoles exposed by running services."
        title="Workbenches"
      >
        {integrationLinks.length > 0 ? (
          <RowList>
            {integrationLinks.map((link) => (
              <a
                className="band band-hover flex h-7 items-center gap-2.5 px-3"
                href={link.url}
                key={`${link.service}:${link.label}:${link.url}`}
                rel="noreferrer"
                target="_blank"
              >
                <span className="text-ink w-16 flex-none truncate font-mono text-[11px] font-bold">
                  {link.label}
                </span>
                <span className="text-ink-soft hidden truncate font-mono text-[10px] md:inline">
                  {link.description} · {link.host}
                </span>
                <span className="flex-1" />
                <ExternalLink className="text-ink-faint size-3 flex-none" />
              </a>
            ))}
          </RowList>
        ) : (
          <EmptyBlock
            description="Workbench links appear once services are running."
            title="No workbenches available"
          />
        )}
      </SectionCard>
    </Page>
  )
}

function stageBandClass(state: string): string {
  switch (state) {
    case 'error':
      return 'bg-status-band-error'
    case 'warning':
      return 'bg-status-band-warning'
    case 'ok':
      return 'bg-status-band-ok'
    default:
      return 'bg-sheet'
  }
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
