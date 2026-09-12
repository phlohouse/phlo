/**
 * /lineage route. Asset-level lineage rendered on the flow canvas, plus
 * table previews, quality checks, and recent operations for the selected
 * asset.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  Activity,
  Database,
  GitBranch,
  Network,
  Search,
  ShieldCheck,
  Table2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryAsset,
  ObservatoryLogEvent,
  ObservatoryOperation,
  ObservatoryQualityCheck,
  ObservatoryTable,
  ObservatoryTablePreview,
} from '@/observatory/api/types'
import type {
  ObservatoryFlowEdge,
  ObservatoryFlowNode,
} from '@/observatory/components/ObservatoryFlowCanvas'
import {
  getObservatoryAssetRecords,
  getObservatoryLogRecords,
  getObservatoryOperationRecords,
  getObservatoryQualityRecords,
  getObservatoryTablePreview,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { ObservatoryFlowCanvas } from '@/observatory/components/ObservatoryFlowCanvas'
import { readMetric, useLiveResource } from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import { InspectorSection } from '@/components/observatory/split-view'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/lineage')({
  component: Lineage,
})

export function Lineage() {
  return <LineageIndex />
}

function LineageIndex() {
  const result = useLiveResource(
    getObservatoryAssetRecords,
    120_000,
    'observatory:assets',
  )
  const tablesResult = useLiveResource(
    getObservatoryTableRecords,
    120_000,
    'observatory:tables',
  )
  const qualityResult = useLiveResource(
    getObservatoryQualityRecords,
    120_000,
    'observatory:quality',
  )
  const logsResult = useLiveResource(
    getObservatoryLogRecords,
    120_000,
    'observatory:logs',
  )
  const operationsResult = useLiveResource(
    getObservatoryOperationRecords,
    120_000,
    'observatory:operations',
  )
  const assets = result.data ?? []
  const tables = tablesResult.data ?? []
  const quality = qualityResult.data ?? []
  const logs = logsResult.data ?? []
  const operations = operationsResult.data ?? []
  const isLoading =
    result.isLoading ||
    tablesResult.isLoading ||
    qualityResult.isLoading ||
    logsResult.isLoading ||
    operationsResult.isLoading
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activeDetail, setActiveDetail] = useState<AssetDetailTab>('overview')
  const [query, setQuery] = useState('')
  const selectAsset = useCallback((assetId: string) => {
    setSelectedId(assetId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('assetId', assetId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])
  const downstreamCounts = useMemo(
    () => buildDownstreamCounts(assets),
    [assets],
  )
  const qualityCounts = useMemo(() => buildQualityCounts(quality), [quality])
  const filteredAssets = useMemo(
    () =>
      filterAssets(assets, query).sort(
        (left, right) =>
          assetScore(right, downstreamCounts, qualityCounts) -
          assetScore(left, downstreamCounts, qualityCounts),
      ),
    [assets, downstreamCounts, qualityCounts, query],
  )
  const selected =
    assets.find((asset) => asset.id === selectedId) ??
    chooseDefaultAsset(
      filteredAssets.length ? filteredAssets : assets,
      assets,
      quality,
    )
  const graph = useMemo(
    () =>
      buildAssetNeighborhood(
        assets,
        selected?.id ?? null,
        qualityCounts,
        downstreamCounts,
      ),
    [assets, downstreamCounts, qualityCounts, selected?.id],
  )
  const qualityChecks = quality.length
  const dependencies = assets.reduce(
    (sum, asset) => sum + asset.dependencies.length,
    0,
  )
  const groups = new Set(assets.map((asset) => asset.group ?? 'ungrouped')).size
  const detail = selected
    ? buildAssetDetail(selected, assets, tables, quality, logs, operations)
    : null
  const primaryTable = detail?.tables[0] ?? null
  const [preview, setPreview] = useState<{
    tableId: string | null
    data: ObservatoryTablePreview | null
    error: string | null
  }>({ tableId: null, data: null, error: null })

  useEffect(() => {
    if (!primaryTable) {
      setPreview({ tableId: null, data: null, error: null })
      return
    }

    let cancelled = false
    getObservatoryTablePreview({
      data: { tableId: primaryTable.id, limit: 5 },
    }).then((response) => {
      if (cancelled) return
      setPreview({
        tableId: primaryTable.id,
        data: response.data,
        error: response.error,
      })
    })

    return () => {
      cancelled = true
    }
  }, [primaryTable?.id])

  const selectedPreview =
    preview.tableId === primaryTable?.id ? preview.data : null
  const selectedPreviewError =
    preview.tableId === primaryTable?.id ? preview.error : null
  const selectedTableStats = primaryTable
    ? tableStats(primaryTable, selectedPreview, selectedPreviewError)
    : null
  const impact = selected && detail ? buildLineageImpact(detail) : null

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get('assetId')
    if (!requested || requested === selectedId) return
    if (assets.some((asset) => asset.id === requested)) {
      setSelectedId(requested)
    }
  }, [assets, selectedId])

  useEffect(() => {
    if (selectedId !== null || !selected) return
    setSelectedId(selected.id)
  }, [selected, selectedId])

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading ? 'Loading' : `${assets.length} mapped dependencies`}
          </Badge>
        }
        description="Trace Dataset dependencies, downstream blast radius, quality evidence, tables, and operational activity."
        title="Lineage"
      />
      <StatGrid className="xl:grid-cols-5">
        <StatCard
          note={
            isLoading
              ? 'Reading live lineage graph'
              : (selected?.id ?? `${assets.length} mapped dependencies`)
          }
          label="Selected dependency"
          value={isLoading ? '—' : (selected?.name ?? 'No dependency selected')}
        />
        <StatCard
          note={
            isLoading ? 'Reading dependencies' : `${dependencies} total links`
          }
          label="Dependencies"
          value={
            isLoading
              ? '—'
              : impact
                ? `${impact.upstream} up / ${impact.downstream} down`
                : dependencies
          }
        />
        <StatCard
          note="Open triage evidence"
          href={impact?.qualityHref ?? undefined}
          label="Quality"
          value={
            isLoading
              ? '—'
              : (impact?.qualityLabel ?? `${qualityChecks} checks`)
          }
        />
        <StatCard
          note={
            isLoading
              ? 'Reading tables'
              : (selectedTableStats?.format ?? `${groups} groups`)
          }
          href={impact?.tableHref ?? undefined}
          label="Bound table"
          value={isLoading ? '—' : (primaryTable?.id ?? 'No table linked')}
        />
        <StatCard
          note="Open run or log evidence"
          href={impact?.operationHref ?? undefined}
          label="Activity"
          value={
            isLoading ? '—' : (impact?.activityLabel ?? 'No linked activity')
          }
        />
      </StatGrid>
      <div className="grid min-h-0 flex-1 grid-cols-[16rem_minmax(0,1fr)_22rem] max-xl:grid-cols-[14rem_minmax(0,1fr)] max-lg:grid-cols-1">
        <div className="bg-card ring-foreground/10 flex min-h-0 flex-col ring-1">
          <div className="flex items-center gap-2 border-b p-2">
            <span className="text-foreground px-1 text-xs font-semibold">
              Lineage index
            </span>
            <div className="relative min-w-0 flex-1">
              <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
              <Input
                aria-label="Search lineage"
                className="pl-7"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search Datasets, tables, groups, checks"
                value={query}
              />
            </div>
          </div>
          <div className="text-muted-foreground grid grid-cols-[minmax(0,1fr)_4.5rem_3.5rem] gap-2 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
            <span>Name</span>
            <span>Quality</span>
            <span>Impact</span>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            {filteredAssets.map((asset) => (
              <button
                className={cn(
                  'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1fr)_4.5rem_3.5rem] items-center gap-2 border-b px-3 py-1.5 text-left transition-colors',
                  asset.id === selected?.id &&
                    'bg-accent/60 hover:bg-accent/60',
                )}
                data-active={asset.id === selected?.id}
                key={asset.id}
                onClick={() => selectAsset(asset.id)}
                type="button"
              >
                <span className="text-foreground truncate text-[11px]">
                  {asset.name}
                </span>
                <span className="text-muted-foreground truncate font-mono text-[10px]">
                  {qualityLabelForAsset(asset, quality)}
                </span>
                <span className="text-muted-foreground font-mono text-[10px] tabular-nums">
                  {downstreamCounts.get(asset.id) ?? 0}
                </span>
              </button>
            ))}
            {filteredAssets.length === 0 && (
              <EmptyBlock
                className="py-8"
                description={
                  isLoading
                    ? 'Reading live dependency and impact evidence.'
                    : 'No dependencies match the current search.'
                }
                title={isLoading ? 'Loading' : 'No dependencies'}
              />
            )}
          </ScrollArea>
        </div>

        <div className="bg-surface-sunken ring-foreground/10 flex min-h-0 flex-col ring-1 max-xl:hidden">
          <div className="bg-card flex items-center justify-between gap-2 border-b px-3 py-2">
            <span className="text-foreground flex items-center gap-1.5 text-xs font-semibold">
              <Network className="text-muted-foreground size-3.5" />
              Neighborhood
            </span>
            <Badge variant="secondary">
              {isLoading ? 'Loading' : `${graph.edges.length} links`}
            </Badge>
          </div>
          {isLoading ? (
            <LoadingBlock
              className="flex-1"
              label="Reading live lineage graph"
            />
          ) : (
            <ObservatoryFlowCanvas
              edges={graph.edges}
              nodes={graph.nodes}
              onSelect={selectAsset}
              selectedId={selected?.id}
            />
          )}
        </div>

        <aside className="bg-card ring-foreground/10 flex min-h-0 flex-col ring-1 max-lg:min-h-[24rem]">
          <ScrollArea className="min-h-0 flex-1">
            {selected ? (
              <>
                <InspectorSection label={selected.group ?? 'Dependency map'}>
                  <h2 className="text-foreground text-sm font-semibold">
                    {selected.name}
                  </h2>
                  <p className="text-muted-foreground text-xs/relaxed">
                    {summarizeDescription(selected.description)}
                  </p>
                  <FactGrid>
                    <Fact
                      label="Downstream"
                      value={downstreamCounts.get(selected.id) ?? 0}
                    />
                    <Fact
                      label="Owner"
                      value={readMetric(selected.metadata, 'owner')}
                    />
                    <Fact
                      label="Records"
                      value={
                        selectedTableStats?.records ??
                        readMetric(selected.metadata, 'records')
                      }
                    />
                    <Fact
                      label="Columns"
                      value={
                        selectedTableStats?.columns ??
                        readMetric(selected.metadata, 'columns')
                      }
                    />
                    <Fact
                      label="Format"
                      value={
                        selectedTableStats?.format ??
                        readMetric(selected.metadata, 'format')
                      }
                    />
                    <Fact
                      label="Namespace"
                      value={
                        selectedTableStats?.namespace ??
                        readMetric(selected.metadata, 'namespace')
                      }
                    />
                  </FactGrid>
                  <div className="flex flex-wrap items-center gap-1.5 pt-1">
                    {selected.dependencies.map((dependency) => (
                      <DependencyChip
                        assets={assets}
                        dependency={dependency}
                        key={dependency}
                        onSelect={selectAsset}
                      />
                    ))}
                    {selected.checks.map((check) => (
                      <Link
                        className="border-input hover:bg-accent inline-flex h-6 items-center gap-1 border px-2 font-mono text-[10px] transition-colors"
                        key={check}
                        search={{ checkId: check }}
                        to="/quality"
                      >
                        <ShieldCheck className="size-3" />
                        {check}
                      </Link>
                    ))}
                  </div>
                </InspectorSection>
                <div
                  aria-label="Dependency detail"
                  className="border-border flex border-b"
                  role="tablist"
                >
                  {assetDetailTabs.map((tab) => (
                    <button
                      aria-selected={activeDetail === tab.id}
                      className={cn(
                        'text-muted-foreground hover:bg-accent/50 flex flex-1 items-center justify-center gap-1 px-2 py-1.5 text-[10px] font-medium tracking-widest uppercase transition-colors',
                        activeDetail === tab.id && 'bg-accent text-foreground',
                      )}
                      data-active={activeDetail === tab.id}
                      key={tab.id}
                      onClick={() => setActiveDetail(tab.id)}
                      role="tab"
                      type="button"
                    >
                      {tab.icon}
                      {tab.label}
                    </button>
                  ))}
                </div>
                <div className="px-3">
                  {detail && (
                    <AssetDetailPanel
                      active={activeDetail}
                      detail={detail}
                      preview={selectedPreview}
                      selected={selected}
                    />
                  )}
                </div>
              </>
            ) : (
              <p className="text-muted-foreground p-3 text-xs">
                {isLoading
                  ? 'Loading dependency detail and evidence.'
                  : 'No dependency evidence is available yet.'}
              </p>
            )}
          </ScrollArea>
          {result.error && (
            <p className="text-status-error border-t px-3 py-2 font-mono text-[10px] break-all">
              {result.error}
            </p>
          )}
        </aside>
      </div>
    </Page>
  )
}

type AssetDetailTab = 'overview' | 'tables' | 'quality' | 'activity'

const assetDetailTabs: Array<{
  id: AssetDetailTab
  label: string
  icon: ReactNode
}> = [
  { id: 'overview', label: 'Overview', icon: <Network className="size-3.5" /> },
  { id: 'tables', label: 'Tables', icon: <Table2 className="size-3.5" /> },
  {
    id: 'quality',
    label: 'Quality',
    icon: <ShieldCheck className="size-3.5" />,
  },
  {
    id: 'activity',
    label: 'Activity',
    icon: <Activity className="size-3.5" />,
  },
]

interface AssetDetailModel {
  upstream: Array<ObservatoryAsset>
  downstream: Array<ObservatoryAsset>
  tables: Array<ObservatoryTable>
  quality: Array<ObservatoryQualityCheck>
  logs: Array<ObservatoryLogEvent>
  operations: Array<ObservatoryOperation>
}

function DetailRow({
  children,
  href,
  meta,
  search,
  title,
  to,
}: {
  children?: ReactNode
  href?: string
  meta?: string
  search?: Record<string, string>
  title: string
  to?: string
}) {
  const className =
    'hover:bg-accent/50 flex items-center justify-between gap-2 border-b px-1 py-2 transition-colors last:border-b-0'
  const body = (
    <>
      <span className="text-foreground flex min-w-0 items-center gap-1.5 truncate text-[11px]">
        {children}
        {title}
      </span>
      {meta && (
        <span className="text-muted-foreground flex-none font-mono text-[10px]">
          {meta}
        </span>
      )}
    </>
  )
  if (to) {
    return (
      <Link className={className} search={search} to={to}>
        {body}
      </Link>
    )
  }
  if (href) {
    return (
      <a className={className} href={href}>
        {body}
      </a>
    )
  }
  return <div className={className}>{body}</div>
}

function AssetDetailPanel({
  active,
  detail,
  preview,
  selected,
}: {
  active: AssetDetailTab
  detail: AssetDetailModel
  preview: ObservatoryTablePreview | null
  selected: ObservatoryAsset
}) {
  if (active === 'tables') {
    return (
      <div className="flex flex-col py-1">
        {detail.tables.length ? (
          detail.tables.map((table) => (
            <DetailRow
              key={table.id}
              meta={
                table.id === preview?.table.id
                  ? [
                      table.format,
                      preview.row_count === null ||
                      preview.row_count === undefined
                        ? null
                        : `${preview.row_count} records`,
                      `${preview.columns.length} columns`,
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : [table.format, table.branch].filter(Boolean).join(' · ') ||
                    'bound table'
              }
              search={{ tableId: table.id }}
              title={
                table.namespace
                  ? `${table.namespace}.${table.name}`
                  : table.name
              }
              to="/tables"
            />
          ))
        ) : (
          <p className="text-muted-foreground py-2 text-xs">
            No bound tables linked to this dependency yet.
          </p>
        )}
      </div>
    )
  }

  if (active === 'quality') {
    return (
      <div className="flex flex-col py-1">
        {detail.quality.length ? (
          detail.quality.map((check) => (
            <DetailRow
              key={check.id}
              meta={[check.status, check.severity].filter(Boolean).join(' · ')}
              search={{ checkId: check.id }}
              title={check.name}
              to="/quality"
            >
              <ShieldCheck className="size-3.5 flex-none" />
            </DetailRow>
          ))
        ) : (
          <p className="text-muted-foreground py-2 text-xs">
            No quality checks linked to this Dataset yet.
          </p>
        )}
      </div>
    )
  }

  if (active === 'activity') {
    const activity = [
      ...detail.operations.map((operation) => ({
        id: `operation:${operation.id}`,
        label: operation.name,
        meta: [operation.kind, operation.status].filter(Boolean).join(' · '),
        href: `/operations?operationId=${encodeURIComponent(operation.id)}`,
      })),
      ...detail.logs.map((log) => ({
        id: `log:${log.id}`,
        label: log.message,
        meta: [log.level, log.source].filter(Boolean).join(' · '),
        href: `/logs?logId=${encodeURIComponent(log.id)}`,
      })),
    ]
    return (
      <div className="flex flex-col py-1">
        {activity.length ? (
          activity.map((item) => (
            <DetailRow
              href={item.href}
              key={item.id}
              meta={item.meta}
              title={item.label}
            />
          ))
        ) : (
          <p className="text-muted-foreground py-2 text-xs">
            No run or log evidence linked to this Dataset yet.
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col py-1">
      {datasetHrefForAsset(selected) && (
        <DetailRow
          href={datasetHrefForAsset(selected) ?? '/datasets'}
          meta={datasetLabelForAsset(selected)}
          title="Open Dataset"
        >
          <Database className="size-3.5 flex-none" />
        </DetailRow>
      )}
      <DetailRow
        meta={detail.upstream.map((asset) => asset.name).join(', ') || 'none'}
        title="Upstream"
      />
      <DetailRow
        meta={detail.downstream.map((asset) => asset.name).join(', ') || 'none'}
        title="Downstream"
      />
      <DetailRow
        meta={selected.resources.join(', ') || 'none'}
        title="External refs"
      />
    </div>
  )
}

function datasetHrefForAsset(asset: ObservatoryAsset): string | null {
  const candidate = asset.metadata.dataset_id
  if (typeof candidate === 'string' && candidate.trim()) {
    return `/datasets/${encodeURIComponent(candidate)}`
  }
  const datasetName = asset.metadata.dataset_name
  if (typeof datasetName === 'string' && datasetName.trim()) {
    return `/datasets/${encodeURIComponent(asset.id)}`
  }
  const publicationState = asset.metadata.publication_state
  if (typeof publicationState === 'string' && publicationState.trim()) {
    return `/datasets/${encodeURIComponent(asset.id)}`
  }
  return null
}

function datasetLabelForAsset(asset: ObservatoryAsset): string {
  const label = asset.metadata.dataset_name
  return typeof label === 'string' && label.trim() ? label : asset.id
}

function DependencyChip({
  assets,
  dependency,
  onSelect,
}: {
  assets: Array<ObservatoryAsset>
  dependency: string
  onSelect: (assetId: string) => void
}) {
  const exists = assets.some((asset) => asset.id === dependency)
  const className =
    'border-input inline-flex h-6 items-center gap-1 border px-2 font-mono text-[10px]'
  const content = (
    <>
      <GitBranch className="size-3" />
      {dependency}
    </>
  )
  if (!exists) {
    return (
      <span className={cn(className, 'text-muted-foreground')}>{content}</span>
    )
  }
  return (
    <button
      className={cn(className, 'hover:bg-accent transition-colors')}
      onClick={() => onSelect(dependency)}
      type="button"
    >
      {content}
    </button>
  )
}

function qualityLabelForAsset(
  asset: ObservatoryAsset,
  quality: Array<ObservatoryQualityCheck>,
): string {
  const checks = quality.filter((check) => check.asset_id === asset.id)
  const failing = checks.filter((check) => check.status === 'failing').length
  if (failing > 0) return `${failing} failing`
  if (checks.length > 0) return String(checks.length)
  return String(asset.checks.length)
}

function buildAssetDetail(
  selected: ObservatoryAsset,
  assets: Array<ObservatoryAsset>,
  tables: Array<ObservatoryTable>,
  quality: Array<ObservatoryQualityCheck>,
  logs: Array<ObservatoryLogEvent>,
  operations: Array<ObservatoryOperation>,
): AssetDetailModel {
  return {
    upstream: assets.filter((asset) =>
      selected.dependencies.includes(asset.id),
    ),
    downstream: assets.filter((asset) =>
      asset.dependencies.includes(selected.id),
    ),
    tables: tables.filter((table) => table.asset_id === selected.id),
    quality: quality.filter((check) => check.asset_id === selected.id),
    logs: logs.filter(
      (log) =>
        log.resource?.kind === 'asset' && log.resource.id === selected.id,
    ),
    operations: operations.filter(
      (operation) =>
        operation.target?.id === selected.id &&
        (operation.target.kind === 'asset' ||
          operation.target.kind === 'table' ||
          operation.target.kind === 'dataset'),
    ),
  }
}

function buildLineageImpact(detail: AssetDetailModel): {
  upstream: number
  downstream: number
  qualityHref: string | null
  qualityLabel: string
  tableHref: string | null
  operationHref: string | null
  activityLabel: string
} {
  const failing = detail.quality.filter((check) => check.status === 'failing')
  const firstQuality = failing[0] ?? detail.quality[0] ?? null
  const firstTable = detail.tables[0] ?? null
  const firstOperation = detail.operations[0] ?? null
  const firstLog = detail.logs[0] ?? null
  return {
    upstream: detail.upstream.length,
    downstream: detail.downstream.length,
    qualityHref: firstQuality
      ? `/quality?checkId=${encodeURIComponent(firstQuality.id)}`
      : null,
    qualityLabel:
      detail.quality.length === 0
        ? 'No checks'
        : `${failing.length} failing / ${detail.quality.length} checks`,
    tableHref: firstTable
      ? `/tables?tableId=${encodeURIComponent(firstTable.id)}`
      : null,
    operationHref: firstOperation
      ? `/operations?operationId=${encodeURIComponent(firstOperation.id)}`
      : firstLog
        ? `/logs?logId=${encodeURIComponent(firstLog.id)}`
        : null,
    activityLabel: firstOperation?.name ?? firstLog?.message ?? 'No activity',
  }
}

function filterAssets(
  assets: Array<ObservatoryAsset>,
  query: string,
): Array<ObservatoryAsset> {
  const needle = query.trim().toLowerCase()
  if (!needle) return assets
  return assets.filter((asset) =>
    [
      asset.name,
      asset.group,
      asset.description,
      ...asset.checks,
      ...asset.dependencies,
    ]
      .filter(Boolean)
      .some((value) => value!.toLowerCase().includes(needle)),
  )
}

function chooseDefaultAsset(
  candidates: Array<ObservatoryAsset>,
  assets: Array<ObservatoryAsset>,
  quality: Array<ObservatoryQualityCheck> = [],
): ObservatoryAsset | null {
  if (!candidates.length) return null
  const downstreamCounts = buildDownstreamCounts(assets)
  const qualityCounts = buildQualityCounts(quality)
  let best = candidates[0]
  let bestScore = assetScore(best, downstreamCounts, qualityCounts)
  for (const candidate of candidates.slice(1)) {
    const score = assetScore(candidate, downstreamCounts, qualityCounts)
    if (score > bestScore) {
      best = candidate
      bestScore = score
    }
  }
  return best
}

function buildDownstreamCounts(
  assets: Array<ObservatoryAsset>,
): Map<string, number> {
  const downstreamCounts = new Map<string, number>()
  assets.forEach((asset) => {
    asset.dependencies.forEach((dependency) => {
      downstreamCounts.set(
        dependency,
        (downstreamCounts.get(dependency) ?? 0) + 1,
      )
    })
  })
  return downstreamCounts
}

function assetScore(
  asset: ObservatoryAsset,
  downstreamCounts: Map<string, number>,
  qualityCounts: Map<string, number>,
): number {
  return (
    asset.dependencies.length * 2 +
    (downstreamCounts.get(asset.id) ?? 0) * 3 +
    (qualityCounts.get(asset.id) ?? asset.checks.length)
  )
}

function buildQualityCounts(
  quality: Array<ObservatoryQualityCheck>,
): Map<string, number> {
  const qualityCounts = new Map<string, number>()
  quality.forEach((check) => {
    qualityCounts.set(
      check.asset_id,
      (qualityCounts.get(check.asset_id) ?? 0) + 1,
    )
  })
  return qualityCounts
}

function buildAssetNeighborhood(
  assets: Array<ObservatoryAsset>,
  selectedId: string | null,
  qualityCounts: Map<string, number>,
  downstreamCounts: Map<string, number>,
): {
  nodes: Array<ObservatoryFlowNode>
  edges: Array<ObservatoryFlowEdge>
} {
  const assetById = new Map(assets.map((asset) => [asset.id, asset]))
  const selected = selectedId ? assetById.get(selectedId) : assets[0]
  if (!selected) return { nodes: [], edges: [] }

  const relatedIds = new Set<string>([selected.id])
  selected.dependencies.forEach((dependency) => relatedIds.add(dependency))
  for (const asset of assets) {
    for (const dependency of asset.dependencies) {
      if (dependency === selected.id) {
        relatedIds.add(asset.id)
        break
      }
    }
  }

  const neighborhood = assets.filter((asset) => relatedIds.has(asset.id))
  const nodes = neighborhood.map(
    (asset): ObservatoryFlowNode => ({
      id: asset.id,
      label: asset.name,
      kind: 'asset',
      lane: assetLane(asset),
      subtitle: asset.description,
      metric: `${qualityCounts.get(asset.id) ?? asset.checks.length} checks · ${downstreamCounts.get(asset.id) ?? 0} down`,
    }),
  )
  const edges = neighborhood.flatMap((asset) => {
    const assetEdges: Array<ObservatoryFlowEdge> = []
    for (const dependency of asset.dependencies) {
      if (!relatedIds.has(dependency)) continue
      assetEdges.push({
        id: `${dependency}->${asset.id}`,
        source: dependency,
        target: asset.id,
      })
    }
    return assetEdges
  })

  return { nodes, edges }
}

function assetLane(asset: ObservatoryAsset): string {
  const group = (asset.group ?? '').toLowerCase()
  const name = asset.name.toLowerCase()
  if (group === 'nightscout' || name.startsWith('dlt_')) return 'raw'
  if (group === 'bronze' || name.startsWith('stg_')) return 'bronze'
  if (group === 'silver') return 'silver'
  if (group === 'gold') return 'gold'
  if (group === 'marts' || name.startsWith('mrt_')) return 'marts'
  return 'other'
}

function summarizeDescription(description?: string | null): string {
  if (!description) return 'No description available.'
  const compact = description.replace(/\s+/g, ' ').trim()
  return compact.length > 220 ? `${compact.slice(0, 217)}...` : compact
}

function tableStats(
  table: ObservatoryTable,
  preview: ObservatoryTablePreview | null,
  error: string | null,
): {
  records: string | number
  columns: string | number
  format: string
  namespace: string
} {
  const records =
    preview?.row_count ??
    readMetric(table.metadata, 'records') ??
    readMetric(table.metadata, 'row_count') ??
    (error ? 'unavailable' : 'unknown')
  const columns =
    preview?.columns.length ??
    readMetric(table.metadata, 'columns') ??
    (error ? 'unavailable' : 'unknown')

  return {
    records,
    columns,
    format: table.format ?? 'table',
    namespace: table.namespace ?? table.schema_name ?? 'default',
  }
}
