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
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { ObservatoryIndexTable } from '@/observatory/components/ObservatoryTable'
import { readMetric, useLiveResource } from '@/observatory/routes/liveResource'

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
    <ObservatoryPage
      kicker="Impact"
      title="Lineage"
      description="Trace Dataset dependencies, downstream blast radius, quality evidence, tables, and operational activity."
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${assets.length} mapped dependencies`}
        </span>
      }
    >
      <section className="phlo-observatory-lineage-summary">
        <LineageSummaryCell
          icon={<Database className="size-4" />}
          label="Selected dependency"
          value={
            isLoading ? 'Loading' : (selected?.name ?? 'No dependency selected')
          }
          detail={
            isLoading
              ? 'Reading live lineage graph'
              : (selected?.id ?? `${assets.length} mapped dependencies`)
          }
        />
        <LineageSummaryCell
          icon={<GitBranch className="size-4" />}
          label="Dependencies"
          value={
            isLoading
              ? 'Loading'
              : impact
                ? `${impact.upstream} up / ${impact.downstream} down`
                : dependencies
          }
          detail={
            isLoading ? 'Reading dependencies' : `${dependencies} total links`
          }
        />
        <LineageSummaryCell
          href={impact?.qualityHref}
          icon={<ShieldCheck className="size-4" />}
          label="Quality"
          value={
            isLoading
              ? 'Loading'
              : (impact?.qualityLabel ?? `${qualityChecks} checks`)
          }
          detail="Open triage evidence"
        />
        <LineageSummaryCell
          href={impact?.tableHref}
          icon={<Table2 className="size-4" />}
          label="Bound table"
          value={
            isLoading ? 'Loading' : (primaryTable?.id ?? 'No table linked')
          }
          detail={
            isLoading
              ? 'Reading tables'
              : (selectedTableStats?.format ?? `${groups} groups`)
          }
        />
        <LineageSummaryCell
          href={impact?.operationHref}
          icon={<Activity className="size-4" />}
          label="Activity"
          value={
            isLoading
              ? 'Loading'
              : (impact?.activityLabel ?? 'No linked activity')
          }
          detail="Open run or log evidence"
        />
      </section>

      <section className="phlo-observatory-assets-workbench">
        <div className="phlo-observatory-asset-index">
          <div className="phlo-observatory-index-toolbar">
            <h2>Lineage index</h2>
            <label className="phlo-observatory-search-field">
              <Search className="size-4" />
              <input
                aria-label="Search lineage"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search Datasets, tables, groups, checks"
                value={query}
              />
            </label>
          </div>
          <ObservatoryIndexTable
            columnTemplate="minmax(150px, 1fr) minmax(72px, 0.42fr) 58px"
            columns={[
              { key: 'name', label: 'Name' },
              { key: 'quality', label: 'Quality' },
              { key: 'impact', label: 'Impact' },
            ]}
            empty={
              <div className="phlo-observatory-empty-state">
                {isLoading
                  ? 'Reading live dependency and impact evidence.'
                  : 'No dependencies match the current search.'}
              </div>
            }
            rows={filteredAssets.map((asset) => ({
              active: asset.id === selected?.id,
              key: asset.id,
              onSelect: () => selectAsset(asset.id),
              cells: [
                asset.name,
                qualityLabelForAsset(asset, quality),
                downstreamCounts.get(asset.id) ?? 0,
              ],
            }))}
            variant="compact"
          />
        </div>

        <div className="phlo-observatory-asset-flow">
          <div className="phlo-observatory-workspace-toolbar">
            <span>
              <Network className="size-4" />
              Neighborhood
            </span>
            <span className="phlo-observatory-pill">
              {isLoading ? 'Loading' : `${graph.edges.length} links`}
            </span>
          </div>
          {isLoading ? (
            <div className="phlo-observatory-flow-canvas">
              <div className="phlo-observatory-flow-empty">
                <Database className="size-4" />
                <span>Reading live lineage graph</span>
              </div>
            </div>
          ) : (
            <ObservatoryFlowCanvas
              edges={graph.edges}
              nodes={graph.nodes}
              onSelect={selectAsset}
              selectedId={selected?.id}
            />
          )}
        </div>

        <aside className="phlo-observatory-asset-detail">
          {selected ? (
            <>
              <div className="phlo-observatory-detail-header">
                <span>{selected.group ?? 'Dependency map'}</span>
                <h2>{selected.name}</h2>
                <p>{summarizeDescription(selected.description)}</p>
              </div>
              <dl className="phlo-observatory-facts">
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
              </dl>
              <div className="phlo-observatory-chip-cloud">
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
                    className="phlo-observatory-chip"
                    key={check}
                    search={{ checkId: check }}
                    to="/quality"
                  >
                    <ShieldCheck className="size-3" />
                    {check}
                  </Link>
                ))}
              </div>
              <div
                className="phlo-observatory-tab-row"
                role="tablist"
                aria-label="Dependency detail"
              >
                {assetDetailTabs.map((tab) => (
                  <button
                    aria-selected={activeDetail === tab.id}
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
              {detail && (
                <AssetDetailPanel
                  active={activeDetail}
                  detail={detail}
                  preview={selectedPreview}
                  selected={selected}
                />
              )}
            </>
          ) : (
            <p>
              {isLoading
                ? 'Loading dependency detail and evidence.'
                : 'No dependency evidence is available yet.'}
            </p>
          )}
          {result.error && (
            <div className="phlo-observatory-panel-footer">{result.error}</div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
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
      <div className="phlo-observatory-detail-list">
        {detail.tables.length ? (
          detail.tables.map((table) => (
            <Link
              className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
              key={table.id}
              search={{ tableId: table.id }}
              to="/tables"
            >
              <span>
                {table.namespace
                  ? `${table.namespace}.${table.name}`
                  : table.name}
              </span>
              <small>
                {table.id === preview?.table.id
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
                    'bound table'}
              </small>
            </Link>
          ))
        ) : (
          <p>No bound tables linked to this dependency yet.</p>
        )}
      </div>
    )
  }

  if (active === 'quality') {
    return (
      <div className="phlo-observatory-detail-list">
        {detail.quality.length ? (
          detail.quality.map((check) => (
            <Link
              className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
              key={check.id}
              search={{ checkId: check.id }}
              to="/quality"
            >
              <span>
                <ShieldCheck className="size-3.5" />
                {check.name}
              </span>
              <small>
                {[check.status, check.severity].filter(Boolean).join(' · ')}
              </small>
            </Link>
          ))
        ) : (
          <p>No quality checks linked to this Dataset yet.</p>
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
      <div className="phlo-observatory-detail-list">
        {activity.length ? (
          activity.map((item) => (
            <Link
              className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
              key={item.id}
              to={item.href}
            >
              <span>{item.label}</span>
              <small>{item.meta}</small>
            </Link>
          ))
        ) : (
          <p>No run or log evidence linked to this Dataset yet.</p>
        )}
      </div>
    )
  }

  return (
    <div className="phlo-observatory-detail-list">
      {datasetHrefForAsset(selected) && (
        <Link
          className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
          to={datasetHrefForAsset(selected) ?? '/datasets'}
        >
          <span>
            <Database className="size-3.5" />
            Open Dataset
          </span>
          <small>{datasetLabelForAsset(selected)}</small>
        </Link>
      )}
      <div className="phlo-observatory-mini-row">
        <span>Upstream</span>
        <small>
          {detail.upstream.map((asset) => asset.name).join(', ') || 'none'}
        </small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Downstream</span>
        <small>
          {detail.downstream.map((asset) => asset.name).join(', ') || 'none'}
        </small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>External refs</span>
        <small>{selected.resources.join(', ') || 'none'}</small>
      </div>
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
  const content = (
    <>
      <GitBranch className="size-3" />
      {dependency}
    </>
  )
  if (!exists) {
    return <span className="phlo-observatory-chip">{content}</span>
  }
  return (
    <button
      className="phlo-observatory-chip"
      onClick={() => onSelect(dependency)}
      type="button"
    >
      {content}
    </button>
  )
}

function LineageSummaryCell({
  detail,
  href,
  icon,
  label,
  value,
}: {
  detail: string
  href?: string | null
  icon: ReactNode
  label: string
  value: string | number
}) {
  const content = (
    <>
      <span>
        {icon}
        {label}
      </span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </>
  )

  if (href) {
    return (
      <Link className="phlo-observatory-lineage-summary-cell" to={href}>
        {content}
      </Link>
    )
  }

  return <div className="phlo-observatory-lineage-summary-cell">{content}</div>
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

function Fact({
  label,
  value,
}: {
  label: string
  value: string | number | boolean | null | undefined
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd>
        {value === null || value === undefined || value === ''
          ? 'unknown'
          : String(value)}
      </dd>
    </>
  )
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
