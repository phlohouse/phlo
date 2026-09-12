/**
 * /tables route. Table explorer with row preview (capped at 100 rows),
 * column metadata, an inline query console with saved queries, and asset
 * lineage on the flow canvas.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  Columns3,
  Database,
  GitBranch,
  Play,
  Rows3,
  Save,
  Search,
  Terminal,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryAsset,
  ObservatoryCapabilities,
  ObservatoryDataset,
  ObservatoryOperation,
  ObservatoryQualityCheck,
  ObservatoryResourceResult,
  ObservatoryTable,
  ObservatoryTablePreview,
} from '@/observatory/api/types'
import type {
  ObservatoryFlowEdge,
  ObservatoryFlowNode,
} from '@/observatory/components/ObservatoryFlowCanvas'
import {
  getObservatoryAssetRecords,
  getObservatoryCapabilities,
  getObservatoryDatasetRecords,
  getObservatoryOperationRecords,
  getObservatoryQualityRecords,
  getObservatorySavedQueries,
  getObservatoryTablePreview,
  getObservatoryTableRecords,
  runObservatoryQuery,
  saveObservatoryQuery,
} from '@/observatory/api/resources'
import { ObservatoryFlowCanvas } from '@/observatory/components/ObservatoryFlowCanvas'
import { ObservatoryIndexTable } from '@/observatory/components/ObservatoryTable'
import {
  loadCachedResource,
  readMetric,
  useLiveResource,
} from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { EmptyBlock } from '@/components/observatory/states'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

const previewLimit = 100

export const Route = createFileRoute('/tables')({
  component: Tables,
})

export function Tables() {
  const result = useLiveResource(
    getObservatoryTableRecords,
    120_000,
    'observatory:tables',
  )
  const assetResult = useLiveResource(
    getObservatoryAssetRecords,
    120_000,
    'observatory:assets',
  )
  const qualityResult = useLiveResource(
    getObservatoryQualityRecords,
    120_000,
    'observatory:quality',
  )
  const operationResult = useLiveResource(
    getObservatoryOperationRecords,
    120_000,
    'observatory:operations',
  )
  const datasetResult = useLiveResource(
    getObservatoryDatasetRecords,
    120_000,
    'observatory:datasets',
  )
  const [freshTables, setFreshTables] =
    useState<Array<ObservatoryTable> | null>(null)
  useEffect(() => {
    let cancelled = false
    async function refreshTables() {
      const response = await window.fetch('/api/observatory/tables')
      if (!response.ok) return
      const payload = (await response.json()) as {
        items?: Array<ObservatoryTable>
      }
      const items = Array.isArray(payload.items) ? payload.items : null
      if (
        !cancelled &&
        items?.some((table) => table.metadata?.catalog_state !== undefined)
      ) {
        setFreshTables(items)
      }
    }
    void refreshTables().catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])
  const tables = freshTables ?? result.data ?? []
  const hasLoadedTables = result.data !== null
  const assets = assetResult.data ?? []
  const datasets = datasetResult.data ?? []
  const quality = qualityResult.data ?? []
  const operations = operationResult.data ?? []
  const sortedTables = useMemo(() => sortTablesForLineage(tables), [tables])
  const [tableQuery, setTableQuery] = useState('')
  const filteredTables = useMemo(
    () => filterTables(sortedTables, tableQuery),
    [sortedTables, tableQuery],
  )
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected =
    sortedTables.find((table) => table.id === selectedId) ??
    chooseDefaultTable(filteredTables.length ? filteredTables : sortedTables) ??
    null
  const selectTable = useCallback((tableId: string) => {
    setSelectedId(tableId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('tableId', tableId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])
  const [activeDetail, setActiveDetail] = useState<DataDetailTab>('sql')
  const [mainView, setMainView] = useState<TableMainView>('rows')
  const [previewRefreshKey, setPreviewRefreshKey] = useState(0)
  const [sql, setSql] = useState('')
  const [queryResult, setQueryResult] = useState<
    ObservatoryResourceResult<{
      columns: Array<string>
      rows: Array<Record<string, unknown>>
      effective_sql: string
      warnings: Array<string>
    }>
  >({ data: null, error: null })
  const [savedQueries, setSavedQueries] = useState<
    ObservatoryResourceResult<
      Array<{
        id: string
        name: string
        sql: string
        branch?: string | null
      }>
    >
  >({ data: [], error: null })
  const [preview, setPreview] = useState<
    ObservatoryResourceResult<ObservatoryTablePreview>
  >({
    data: null,
    error: null,
  })
  const [capabilities, setCapabilities] =
    useState<ObservatoryResourceResult<ObservatoryCapabilities> | null>(null)
  const [isLoadingMoreRows, setIsLoadingMoreRows] = useState(false)
  const namespaces = new Set(
    tables.map((table) => table.namespace ?? 'default'),
  )
  const graph = useMemo(() => buildTableGraph(tables, assets), [assets, tables])
  const branchesAvailable = capabilities?.data?.features.branches === true
  const selectedPreview =
    preview.data && selected && preview.data.table.id === selected.id
      ? preview.data
      : null
  const selectedProfile = useMemo(
    () =>
      selected
        ? buildTableProfile(
            selected,
            selectedPreview,
            assets,
            quality,
            operations,
          )
        : null,
    [assets, operations, quality, selected, selectedPreview],
  )
  const selectedDataset = selected ? datasetForTable(selected, datasets) : null
  const selectedQuality = selected ? qualityForTable(selected, quality) : []
  const selectedOperations = selected
    ? operationsForTable(selected, selectedDataset, operations)
    : []
  const tableSummary = useMemo(
    () => buildTableSummary(tables, datasets, quality, selected),
    [datasets, quality, selected, tables],
  )
  const selectedPreviewError = selectedPreview ? preview.error : null
  const selectedRowCount =
    selectedPreview && selected ? selectedPreview.row_count : null
  const applySelectedPreview = useCallback(
    (
      nextSql: string,
      nextPreview: ObservatoryResourceResult<ObservatoryTablePreview>,
    ) => {
      setSql(nextSql)
      setPreview(nextPreview)
    },
    [],
  )
  const applyPreview = useCallback(
    (nextPreview: ObservatoryResourceResult<ObservatoryTablePreview>) =>
      setPreview(nextPreview),
    [],
  )

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    let retryTimer: number | undefined
    setPreview({ data: null, error: null })
    setSql(defaultSqlForTable(selected))
    const key = `observatory:table-preview:${selected.id}:${previewLimit}:0:${previewRefreshKey}`
    const loadPreview = (force = false) =>
      loadCachedResource(
        key,
        () =>
          getObservatoryTablePreview({
            data: { tableId: selected.id, limit: previewLimit, offset: 0 },
          }),
        {
          force,
          staleMs: 120_000,
        },
      )

    void loadPreview(true).then((next) => {
      if (cancelled) return
      applySelectedPreview(defaultSqlForTable(selected), next)
      if (isTransientPreviewMiss(next.error)) {
        retryTimer = window.setTimeout(() => {
          void loadPreview(true).then((retry) => {
            if (!cancelled) applyPreview(retry)
          })
        }, 750)
      }
    })
    return () => {
      cancelled = true
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
    }
  }, [applyPreview, applySelectedPreview, previewRefreshKey, selected])

  const loadMoreRows = useCallback(() => {
    if (!selected || isLoadingMoreRows) return
    const current = selectedPreview
    if (!current?.has_more) return
    const offset = current.rows.length
    setIsLoadingMoreRows(true)
    const key = `observatory:table-preview:${selected.id}:${previewLimit}:${offset}:${previewRefreshKey}`
    void loadCachedResource(
      key,
      () =>
        getObservatoryTablePreview({
          data: { tableId: selected.id, limit: previewLimit, offset },
        }),
      { staleMs: 120_000 },
    ).then((next) => {
      setIsLoadingMoreRows(false)
      setPreview((existing) => {
        if (next.error || !next.data) {
          return { data: existing.data, error: next.error }
        }
        if (!existing.data) return next
        if (existing.data.table.id !== next.data.table.id) return existing
        return {
          data: mergeTablePreviews(existing.data, next.data),
          error: null,
        }
      })
    })
  }, [isLoadingMoreRows, previewRefreshKey, selected, selectedPreview])

  useEffect(() => {
    void loadCachedResource(
      'observatory:saved-queries',
      getObservatorySavedQueries,
      {
        staleMs: 300_000,
      },
    ).then(setSavedQueries)
    void loadCachedResource(
      'observatory:capabilities',
      getObservatoryCapabilities,
      {
        staleMs: 120_000,
      },
    ).then(setCapabilities)
  }, [])
  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get('tableId')
    if (!requested || requested === selectedId) return
    if (sortedTables.some((table) => table.id === requested)) {
      setSelectedId(requested)
    }
  }, [selectedId, sortedTables])

  useEffect(() => {
    if (selectedId !== null || !selected) return
    setSelectedId(selected.id)
  }, [selected, selectedId])

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">{namespaces.size} namespaces</Badge>
        }
        description={
          branchesAvailable
            ? 'Inspect physical tables, branches, schemas, Dataset bindings, quality checks, and linked runs.'
            : 'Inspect physical tables, schemas, Dataset bindings, quality checks, and linked runs.'
        }
        title="Table inventory"
      />
      <SplitView
        inspector={
          <>
            <InspectorSection
              label={`Table inspector · ${selected?.name ?? 'none'}`}
            >
              {selected ? (
                <>
                  <p className="text-muted-foreground text-xs/relaxed">
                    {selectedDataset
                      ? `Bound to ${selectedDataset.name}.`
                      : selected.asset_id
                        ? `Source binding ${selected.asset_id}.`
                        : 'No Dataset or lineage binding.'}
                  </p>
                  <FactGrid>
                    <Fact
                      label="Schema"
                      value={selected.schema_name ?? 'not reported'}
                    />
                    <Fact
                      label="Namespace"
                      value={selected.namespace ?? 'default'}
                    />
                    <Fact label="Format" value={selected.format ?? 'unknown'} />
                    {branchesAvailable && (
                      <Fact label="Branch" value={selected.branch ?? 'main'} />
                    )}
                    <Fact
                      label="Queryable state"
                      value={tableCatalogState(selected)}
                    />
                  </FactGrid>
                  <div className="border-border grid grid-cols-2 divide-x border-y">
                    <div className="text-foreground flex items-center gap-1.5 px-3 py-2 font-mono text-[11px]">
                      <Rows3 className="text-muted-foreground size-3.5" />
                      {selectedPreview?.row_count ??
                        readMetric(selected.metadata, 'records') ??
                        'unknown'}{' '}
                      records
                    </div>
                    <div className="text-foreground flex items-center gap-1.5 px-3 py-2 font-mono text-[11px]">
                      <Columns3 className="text-muted-foreground size-3.5" />
                      {selectedPreview?.columns.length
                        ? selectedPreview.columns.length
                        : 'unknown'}{' '}
                      columns
                    </div>
                  </div>
                </>
              ) : (
                <p className="text-muted-foreground text-xs">
                  No table selected.
                </p>
              )}
            </InspectorSection>
            {selected && (
              <>
                <InspectorSection label="Workflow">
                  <TableWorkflowLinks
                    dataset={selectedDataset}
                    operations={selectedOperations}
                    quality={selectedQuality}
                    selected={selected}
                  />
                </InspectorSection>
                <InspectorSection label="Detail">
                  <div
                    aria-label="Table detail"
                    className="flex items-center gap-1.5"
                    role="tablist"
                  >
                    {dataDetailTabs.map((tab) => (
                      <Button
                        aria-selected={activeDetail === tab.id}
                        key={tab.id}
                        onClick={() => setActiveDetail(tab.id)}
                        role="tab"
                        size="xs"
                        type="button"
                        variant={
                          activeDetail === tab.id ? 'default' : 'outline'
                        }
                      >
                        {tab.icon}
                        {tab.label}
                      </Button>
                    ))}
                  </div>
                  <div className="pt-2">
                    <DataDetailPanel
                      active={activeDetail}
                      dataset={selectedDataset}
                      operations={selectedOperations}
                      preview={selectedPreview}
                      quality={selectedQuality}
                      queryResult={queryResult}
                      selected={selected}
                      onRefresh={() => setPreviewRefreshKey((key) => key + 1)}
                      onRunQuery={(nextSql) => {
                        const request = {
                          sql: nextSql,
                          limit: 100,
                          ...(branchesAvailable
                            ? { branch: selected.branch ?? 'main' }
                            : {}),
                        }
                        void runObservatoryQuery({
                          data: request,
                        }).then(setQueryResult)
                      }}
                      onSaveQuery={(nextSql, name) => {
                        const request = {
                          name,
                          sql: nextSql,
                          ...(branchesAvailable
                            ? { branch: selected.branch ?? 'main' }
                            : {}),
                        }
                        void saveObservatoryQuery({
                          data: request,
                        }).then((next) => {
                          if (next.data) {
                            setSavedQueries((current) => ({
                              data: [next.data!, ...(current.data ?? [])],
                              error: null,
                            }))
                          } else {
                            setSavedQueries((current) => ({
                              data: current.data,
                              error: next.error,
                            }))
                          }
                        })
                      }}
                      savedQueries={savedQueries.data ?? []}
                      showBranch={branchesAvailable}
                      setSql={setSql}
                      sql={sql}
                    />
                  </div>
                  {selectedPreviewError && (
                    <p className="text-status-error pt-2 font-mono text-[10px] break-all">
                      {selectedPreviewError}
                    </p>
                  )}
                </InspectorSection>
              </>
            )}
            {(result.error ??
              qualityResult.error ??
              operationResult.error ??
              datasetResult.error) && (
              <InspectorSection label="Errors">
                {[
                  result.error,
                  qualityResult.error,
                  operationResult.error,
                  datasetResult.error,
                ]
                  .filter(Boolean)
                  .map((error) => (
                    <p
                      className="text-status-error font-mono text-[10px] break-all"
                      key={error}
                    >
                      {error}
                    </p>
                  ))}
              </InspectorSection>
            )}
          </>
        }
        inspectorWidth="w-[24rem]"
        list={
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
            <SectionCard
              actions={
                <Badge variant="secondary">
                  {filteredTables.length} / {tables.length} tables
                </Badge>
              }
              title="Inventory"
            >
              <div className="border-border border-b px-3 py-2">
                <label className="relative block">
                  <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
                  <Input
                    aria-label="Search tables"
                    className="pl-8"
                    onChange={(event) => setTableQuery(event.target.value)}
                    placeholder={
                      branchesAvailable
                        ? 'Search name, namespace, branch'
                        : 'Search name, namespace, schema'
                    }
                    value={tableQuery}
                  />
                </label>
              </div>
              <TableInventorySummary summary={tableSummary} />
              <ScrollArea className="max-h-72">
                <ObservatoryIndexTable
                  columnTemplate={
                    branchesAvailable
                      ? '1.15fr 0.8fr 1.25fr 0.65fr 0.65fr 0.9fr 0.9fr'
                      : '1.15fr 0.8fr 1.25fr 0.65fr 0.8fr 0.9fr'
                  }
                  columns={[
                    { key: 'name', label: 'Name' },
                    { key: 'namespace', label: 'Namespace' },
                    { key: 'dataset', label: 'Dataset' },
                    { key: 'format', label: 'Format' },
                    ...(branchesAvailable
                      ? [{ key: 'branch', label: 'Branch' }]
                      : []),
                    { key: 'rows', label: 'Rows' },
                    { key: 'queryable', label: 'Queryable state' },
                  ]}
                  empty={
                    <EmptyBlock
                      description={
                        !hasLoadedTables
                          ? 'Loading tables...'
                          : tables.length === 0
                            ? 'No tables registered yet.'
                            : 'No tables match this filter.'
                      }
                      title="Table inventory"
                    />
                  }
                  rows={filteredTables.map((table) => {
                    const rowDataset = datasetForTable(table, datasets)
                    const rowCount =
                      table.id === selected?.id && selectedRowCount !== null
                        ? selectedRowCount
                        : (readTableRecordCount(table) ?? '-')
                    return {
                      active: table.id === selected?.id,
                      key: table.id,
                      onSelect: () => selectTable(table.id),
                      cells: [
                        table.name,
                        table.namespace ?? 'default',
                        tableDatasetLabel(rowDataset),
                        table.format ?? 'unknown',
                        ...(branchesAvailable ? [table.branch ?? 'main'] : []),
                        rowCount,
                        tableCatalogState(table),
                      ],
                    }
                  })}
                />
              </ScrollArea>
            </SectionCard>
            {selected && selectedProfile && (
              <TableEvidenceBand
                dataset={selectedDataset}
                operations={selectedOperations}
                profile={selectedProfile}
                quality={selectedQuality}
                selected={selected}
              />
            )}
            <div
              aria-label="Table main view"
              className="flex items-center gap-1.5"
              role="tablist"
            >
              {dataMainViews.map((view) => (
                <Button
                  aria-selected={mainView === view.id}
                  key={view.id}
                  onClick={() => setMainView(view.id)}
                  role="tab"
                  size="xs"
                  type="button"
                  variant={mainView === view.id ? 'default' : 'outline'}
                >
                  {view.icon}
                  {view.label}
                </Button>
              ))}
            </div>
            {mainView === 'lineage' ? (
              <SectionCard
                actions={
                  <Badge variant="secondary">
                    {graph.edges.length} bindings
                  </Badge>
                }
                contentClassName="p-0"
                title="Table lineage"
              >
                <div className="bg-paper min-h-[26rem]">
                  <ObservatoryFlowCanvas
                    edges={graph.edges}
                    nodes={graph.nodes}
                    onSelect={selectTable}
                    selectedId={selected?.id}
                  />
                </div>
              </SectionCard>
            ) : (
              <DataPreviewTable
                isLoadingMoreRows={isLoadingMoreRows}
                onLoadMoreRows={loadMoreRows}
                mode={mainView}
                preview={selectedPreview}
                selected={selected}
              />
            )}
          </div>
        }
      />
    </Page>
  )
}

type DataDetailTab = 'preview' | 'sql' | 'journey'
type TableMainView = 'rows' | 'schema' | 'lineage'

type TableSummary = {
  total: number
  queryable: number
  datasetBound: number
  qualityLinked: number
  selectedLabel: string
  selectedCatalog: string
}

const dataMainViews: Array<{
  id: TableMainView
  label: string
  icon: ReactNode
}> = [
  { id: 'rows', label: 'Rows', icon: <Rows3 className="size-3.5" /> },
  { id: 'schema', label: 'Schema', icon: <Columns3 className="size-3.5" /> },
  { id: 'lineage', label: 'Lineage', icon: <GitBranch className="size-3.5" /> },
]

const dataDetailTabs: Array<{
  id: DataDetailTab
  label: string
  icon: ReactNode
}> = [
  { id: 'preview', label: 'Preview', icon: <Rows3 className="size-3.5" /> },
  { id: 'sql', label: 'SQL', icon: <Terminal className="size-3.5" /> },
  { id: 'journey', label: 'Journey', icon: <GitBranch className="size-3.5" /> },
]

type TableProfile = {
  stage: string
  records: string | number | boolean | null
  columns: number | null
  upstream: number
  downstream: number
  qualityLabel: string
  qualityState: 'ok' | 'warning' | 'error' | 'unknown'
  latestOperation: ObservatoryOperation | null
  businessKeys: Array<string>
}

function TableInventorySummary({ summary }: { summary: TableSummary }) {
  return (
    <div className="border-border grid grid-cols-5 divide-x border-b max-lg:grid-cols-3">
      <SummaryCell
        label="Queryable"
        value={`${summary.queryable}/${summary.total}`}
      />
      <SummaryCell label="Dataset bindings" value={summary.datasetBound} />
      <SummaryCell label="Quality linked" value={summary.qualityLinked} />
      <SummaryCell label="Selected table" value={summary.selectedLabel} />
      <SummaryCell label="Table state" value={summary.selectedCatalog} />
    </div>
  )
}

function SummaryCell({
  label,
  value,
}: {
  label: string
  value: string | number
}) {
  return (
    <div className="flex flex-col gap-0.5 px-3 py-2">
      <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
        {label}
      </span>
      <strong className="text-foreground truncate font-mono text-[11px]">
        {value}
      </strong>
    </div>
  )
}

function TableEvidenceBand({
  dataset,
  operations,
  profile,
  quality,
  selected,
}: {
  dataset: ObservatoryDataset | null
  operations: Array<ObservatoryOperation>
  profile: TableProfile
  quality: Array<ObservatoryQualityCheck>
  selected: ObservatoryTable
}) {
  const failedQuality = quality.filter((check) => check.status === 'failing')
  const firstQuality = failedQuality[0] ?? quality[0] ?? null
  const latestOperation = operations[0] ?? profile.latestOperation
  const qualityValue =
    quality.length === 0
      ? 'No checks'
      : failedQuality.length > 0 && firstQuality
        ? `${firstQuality.name} failing`
        : `${quality.length} checks passing`

  return (
    <section
      className="bg-sheet border-rule border"
      data-state={profile.qualityState}
    >
      <div className="border-border flex items-center justify-between gap-2 border-b px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
            Selected table
          </span>
          <strong className="text-foreground truncate font-mono text-[11px]">
            {selected.id}
          </strong>
        </div>
        <Badge variant="secondary">{profile.stage} layer</Badge>
      </div>
      <div className="border-border grid grid-cols-5 divide-x border-b max-lg:grid-cols-3">
        <ProfileFact
          href={
            dataset ? `/datasets/${encodeURIComponent(dataset.id)}` : undefined
          }
          label="Dataset"
          value={dataset ? dataset.name : 'Candidate table'}
        />
        <ProfileFact
          href={
            firstQuality
              ? `/quality?checkId=${encodeURIComponent(firstQuality.id)}`
              : undefined
          }
          label="Quality"
          value={qualityValue}
        />
        <ProfileFact
          href={
            selected.asset_id
              ? `/lineage?assetId=${encodeURIComponent(selected.asset_id)}`
              : undefined
          }
          label="Lineage"
          value={`${profile.upstream} up / ${profile.downstream} down`}
        />
        <ProfileFact
          href={
            latestOperation
              ? `/operations?operationId=${encodeURIComponent(latestOperation.id)}`
              : undefined
          }
          label="Operation"
          value={latestOperation?.name ?? 'No operation linked'}
        />
        <ProfileFact label="Rows" value={profile.records ?? 'unknown'} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2">
        {profile.businessKeys.length > 0 ? (
          profile.businessKeys.map((key) => (
            <Badge key={key} variant="secondary">
              {key}
            </Badge>
          ))
        ) : (
          <Badge variant="secondary">No key columns detected</Badge>
        )}
      </div>
    </section>
  )
}

function ProfileFact({
  href,
  label,
  value,
}: {
  href?: string
  label: string
  value: string | number | boolean
}) {
  const content = (
    <>
      <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
        {label}
      </span>
      <strong className="text-foreground truncate text-[11px]">
        {String(value)}
      </strong>
    </>
  )
  const className =
    'flex min-w-0 flex-col gap-0.5 px-3 py-2 hover:bg-accent/50 transition-colors'

  if (href) {
    return (
      <Link className={className} to={href}>
        {content}
      </Link>
    )
  }

  return <div className={cn(className, 'hover:bg-transparent')}>{content}</div>
}

function DataPreviewTable({
  isLoadingMoreRows,
  mode,
  onLoadMoreRows,
  preview,
  selected,
}: {
  isLoadingMoreRows: boolean
  mode: Exclude<TableMainView, 'lineage'>
  onLoadMoreRows: () => void
  preview: ObservatoryTablePreview | null
  selected: ObservatoryTable | null
}) {
  const columns = preview?.columns ?? []
  const rows = preview?.rows ?? []

  if (!selected) {
    return (
      <SectionCard>
        <EmptyBlock
          description="Select a table to inspect rows and schema."
          title="No table selected"
        />
      </SectionCard>
    )
  }

  if (mode === 'schema') {
    return (
      <SectionCard
        actions={<Badge variant="secondary">{columns.length} columns</Badge>}
        contentClassName="p-0"
        title={`${selected.name} schema`}
      >
        <div role="table">
          <div
            className="border-border text-muted-foreground grid grid-cols-[minmax(0,1fr)_minmax(0,8rem)] border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase"
            role="row"
          >
            <span>Column</span>
            <span>Type</span>
          </div>
          <ScrollArea className="max-h-[26rem]">
            <div className="divide-border divide-y">
              {columns.map((column, index) => (
                <div
                  className="grid grid-cols-[minmax(0,1fr)_minmax(0,8rem)] px-3 py-1.5"
                  key={column}
                  role="row"
                >
                  <span className="text-foreground truncate font-mono text-[11px]">
                    {column}
                  </span>
                  <span className="text-muted-foreground truncate font-mono text-[10px]">
                    {columnTypeFor(preview, column, index)}
                  </span>
                </div>
              ))}
              {columns.length === 0 && (
                <EmptyBlock
                  description="No schema preview available yet."
                  title="Schema"
                />
              )}
            </div>
          </ScrollArea>
        </div>
      </SectionCard>
    )
  }

  return (
    <SectionCard
      actions={
        <Badge variant="secondary">
          {rows.length} loaded
          {preview?.row_count ? ` · ${preview.row_count} total` : ''}
        </Badge>
      }
      contentClassName="p-0"
      title={`${selected.name} rows`}
    >
      {columns.length > 0 ? (
        <div
          className="max-h-[30rem] overflow-auto"
          onScroll={(event) => {
            const target = event.currentTarget
            const remaining =
              target.scrollHeight - target.scrollTop - target.clientHeight
            if (remaining < 96) onLoadMoreRows()
          }}
        >
          <table className="w-full border-collapse font-mono text-[11px]">
            <thead className="bg-sheet sticky top-0">
              <tr>
                {columns.map((column) => (
                  <th
                    className="border-border text-muted-foreground border-b px-3 py-1.5 text-left text-[9px] font-medium tracking-widest uppercase"
                    key={column}
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-border divide-y">
              {rows.map((row, index) => (
                <tr key={String(row._phlo_row_id ?? index)}>
                  {columns.map((column) => (
                    <td
                      className="text-foreground max-w-64 truncate px-3 py-1.5"
                      key={column}
                    >
                      {formatCell(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td
                    className="text-muted-foreground px-3 py-4 text-center text-xs"
                    colSpan={columns.length}
                  >
                    No rows matched the active query.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {(preview?.has_more || isLoadingMoreRows) && (
            <div className="border-border border-t px-3 py-2">
              <Button
                disabled={isLoadingMoreRows}
                onClick={onLoadMoreRows}
                size="sm"
                type="button"
                variant="outline"
              >
                {isLoadingMoreRows ? 'Loading more rows…' : 'Load more rows'}
              </Button>
            </div>
          )}
        </div>
      ) : (
        <EmptyBlock
          description={
            preview ? previewEmptyCopy(selected) : 'Loading preview rows…'
          }
          title="Row preview"
        />
      )}
    </SectionCard>
  )
}

function LinkedMiniRow({
  children,
  detail,
  label,
  state,
  to,
  params,
  search,
}: {
  children?: ReactNode
  detail?: string
  label: ReactNode
  state?: string
  to: string
  params?: Record<string, string>
  search?: Record<string, string>
}) {
  return (
    <Link
      className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
      data-state={state}
      params={params}
      search={search}
      to={to}
    >
      <span className="text-foreground flex min-w-0 items-center gap-1.5 truncate text-[11px]">
        {children}
        {label}
      </span>
      {detail && (
        <span className="text-muted-foreground flex-none text-right font-mono text-[10px]">
          {detail}
        </span>
      )}
    </Link>
  )
}

function MiniRow({
  detail,
  label,
  state,
}: {
  detail: string | number
  label: string
  state?: string
}) {
  return (
    <div
      className="flex items-center justify-between gap-2 px-3 py-2"
      data-state={state}
    >
      <span className="text-foreground min-w-0 text-[11px]">{label}</span>
      <span className="text-muted-foreground flex-none text-right font-mono text-[10px]">
        {detail}
      </span>
    </div>
  )
}

function TableWorkflowLinks({
  dataset,
  operations,
  quality,
  selected,
}: {
  dataset: ObservatoryDataset | null
  operations: Array<ObservatoryOperation>
  quality: Array<ObservatoryQualityCheck>
  selected: ObservatoryTable
}) {
  return (
    <div className="divide-border -mx-3 divide-y border-y">
      {dataset ? (
        <LinkedMiniRow
          detail={[
            dataset.name,
            dataset.publication_state,
            dataset.readiness_state,
          ]
            .filter(Boolean)
            .join(' · ')}
          label="Open Dataset"
          params={{ datasetId: dataset.id }}
          to="/datasets/$datasetId"
        >
          <Database className="size-3.5 flex-none" />
        </LinkedMiniRow>
      ) : (
        <LinkedMiniRow
          detail="Open Datasets to claim or promote this table"
          label="Bind to Dataset"
          state="unknown"
          to="/datasets"
        >
          <Database className="size-3.5 flex-none" />
        </LinkedMiniRow>
      )}
      {selected.asset_id ? (
        <LinkedMiniRow
          detail={selected.asset_id}
          label="Open Lineage"
          search={{ assetId: selected.asset_id }}
          to="/lineage"
        >
          <GitBranch className="size-3.5 flex-none" />
        </LinkedMiniRow>
      ) : (
        <LinkedMiniRow
          detail="Open Lineage to connect upstream and downstream impact"
          label="Attach lineage evidence"
          state="unknown"
          to="/lineage"
        >
          <GitBranch className="size-3.5 flex-none" />
        </LinkedMiniRow>
      )}
      {quality.slice(0, 3).map((check) => (
        <LinkedMiniRow
          detail={[check.status, check.severity].filter(Boolean).join(' · ')}
          key={check.id}
          label={check.name}
          search={{ checkId: check.id }}
          to="/quality"
        />
      ))}
      {quality.length === 0 && (
        <LinkedMiniRow
          detail="Open Quality to add freshness, schema, or reconciliation evidence"
          label="Add quality coverage"
          state="unknown"
          to="/quality"
        />
      )}
      {operations.slice(0, 2).map((operation) => (
        <LinkedMiniRow
          detail={[operation.kind, operation.status]
            .filter(Boolean)
            .join(' · ')}
          key={operation.id}
          label={operation.name}
          search={{ operationId: operation.id }}
          to="/operations"
        />
      ))}
      {operations.length === 0 && (
        <LinkedMiniRow
          detail="Open Operations to link refresh, materialization, or recovery runs"
          label="Connect operation evidence"
          state="unknown"
          to="/operations"
        />
      )}
    </div>
  )
}

function mergeTablePreviews(
  current: ObservatoryTablePreview,
  next: ObservatoryTablePreview,
): ObservatoryTablePreview {
  return {
    ...next,
    columns: next.columns.length ? next.columns : current.columns,
    column_types: next.column_types.length
      ? next.column_types
      : current.column_types,
    offset: current.offset,
    rows: [...current.rows, ...next.rows],
  }
}

function columnTypeFor(
  preview: ObservatoryTablePreview | null,
  column: string,
  index: number,
): string {
  const explicitType = preview?.column_types?.[index]
  if (typeof explicitType === 'string' && explicitType.trim()) {
    return explicitType
  }

  const sampledValue = preview?.rows.find(
    (row) => row[column] !== null && row[column] !== undefined,
  )?.[column]
  if (typeof sampledValue === 'number') {
    return Number.isInteger(sampledValue) ? 'integer' : 'double'
  }
  if (typeof sampledValue === 'boolean') {
    return 'boolean'
  }
  if (typeof sampledValue === 'string') {
    return 'varchar'
  }
  if (Array.isArray(sampledValue)) {
    return 'array'
  }
  if (typeof sampledValue === 'object' && sampledValue !== null) {
    return 'object'
  }
  return 'unknown'
}

function DataDetailPanel({
  active,
  dataset,
  onRefresh,
  onRunQuery,
  onSaveQuery,
  operations,
  preview,
  quality,
  queryResult,
  savedQueries,
  selected,
  showBranch,
  setSql,
  sql,
}: {
  active: DataDetailTab
  dataset: ObservatoryDataset | null
  onRefresh: () => void
  onRunQuery: (sql: string) => void
  onSaveQuery: (sql: string, name: string) => void
  operations: Array<ObservatoryOperation>
  preview: ObservatoryTablePreview | null
  quality: Array<ObservatoryQualityCheck>
  queryResult: ObservatoryResourceResult<{
    columns: Array<string>
    rows: Array<Record<string, unknown>>
    effective_sql: string
    warnings: Array<string>
  }>
  savedQueries: Array<{
    id: string
    name: string
    sql: string
    branch?: string | null
  }>
  selected: ObservatoryTable
  showBranch: boolean
  setSql: (value: string) => void
  sql: string
}) {
  const [savedQueryName, setSavedQueryName] = useState('')

  if (active === 'sql') {
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
            Preview query
          </span>
          <Badge variant="secondary">
            {preview?.limit ?? previewLimit} row limit
          </Badge>
        </div>
        <textarea
          className="border-input bg-paper text-foreground focus-visible:ring-ring min-h-24 w-full border px-2 py-1.5 font-mono text-[11px] outline-none focus-visible:ring-1"
          onChange={(event) => setSql(event.target.value)}
          value={sql}
        />
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground text-[11px] font-medium">
            Saved query name
          </span>
          <Input
            onChange={(event) => setSavedQueryName(event.target.value)}
            placeholder="Daily revenue sample"
            value={savedQueryName}
          />
        </label>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button onClick={() => onRunQuery(sql)} size="sm" type="button">
            <Play className="size-3.5" />
            Run query
          </Button>
          <Button onClick={onRefresh} size="sm" type="button" variant="outline">
            <Play className="size-3.5" />
            Refresh preview
          </Button>
          <Button
            disabled={!sql.trim() || !savedQueryName.trim()}
            onClick={() => {
              onSaveQuery(sql, savedQueryName.trim())
              setSavedQueryName('')
            }}
            size="sm"
            type="button"
            variant="outline"
          >
            <Save className="size-3.5" />
            Save
          </Button>
        </div>
        {savedQueries.length > 0 && (
          <div className="divide-border -mx-3 divide-y border-y">
            {savedQueries.slice(0, 4).map((query) => (
              <button
                className="hover:bg-accent/50 flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors"
                key={query.id}
                onClick={() => setSql(query.sql)}
                type="button"
              >
                <span className="text-foreground truncate text-[11px]">
                  {query.name}
                </span>
                <span className="text-muted-foreground flex-none font-mono text-[10px]">
                  {showBranch ? (query.branch ?? 'main') : 'saved'}
                </span>
              </button>
            ))}
          </div>
        )}
        {queryResult.data && (
          <div className="divide-border -mx-3 divide-y border-y">
            <MiniRow
              detail={queryResult.data.effective_sql}
              label="Effective SQL"
            />
            <MiniRow
              detail={String(queryResult.data.rows.length)}
              label="Rows"
            />
          </div>
        )}
        {queryResult.error && (
          <p className="text-status-error font-mono text-[10px] break-all">
            {queryResult.error}
          </p>
        )}
      </div>
    )
  }

  if (active === 'journey') {
    const failingQuality = quality.find((check) => check.status === 'failing')
    const nextQuality = failingQuality ?? quality[0] ?? null
    const latestOperation = operations[0] ?? null
    const owner = dataset?.owner ?? readMetric(selected.metadata, 'owner')

    return (
      <div className="divide-border -mx-3 divide-y border-y">
        <MiniRow detail={owner ?? 'No owner assigned'} label="Owner" />
        <MiniRow
          detail={dataset ? dataset.name : 'Candidate table'}
          label="Dataset binding"
        />
        {dataset ? (
          <LinkedMiniRow
            detail={[dataset.publication_state, dataset.readiness_state].join(
              ' · ',
            )}
            label="Open Dataset readiness"
            params={{ datasetId: dataset.id }}
            to="/datasets/$datasetId"
          />
        ) : (
          <LinkedMiniRow
            detail="Promote this table into a governed Dataset workflow"
            label="Claim Dataset candidate"
            state="unknown"
            to="/datasets"
          />
        )}
        {showBranch && (
          <MiniRow detail={selected.branch ?? 'main'} label="Branch" />
        )}
        {selected.asset_id && (
          <LinkedMiniRow
            detail={selected.asset_id}
            label="Open dependency map"
            search={{ assetId: selected.asset_id }}
            to="/lineage"
          />
        )}
        {nextQuality ? (
          <LinkedMiniRow
            detail={[nextQuality.name, nextQuality.severity]
              .filter(Boolean)
              .join(' · ')}
            label={
              nextQuality.status === 'failing'
                ? 'Triage quality failure'
                : 'Review quality evidence'
            }
            search={{ checkId: nextQuality.id }}
            state={
              nextQuality.status === 'failing' ? 'error' : nextQuality.status
            }
            to="/quality"
          />
        ) : (
          <LinkedMiniRow
            detail="No checks are attached to this table yet"
            label="Add quality evidence"
            state="unknown"
            to="/quality"
          />
        )}
        {latestOperation ? (
          <LinkedMiniRow
            detail={[latestOperation.name, latestOperation.status]
              .filter(Boolean)
              .join(' · ')}
            label="Review latest operation"
            search={{ operationId: latestOperation.id }}
            to="/operations"
          />
        ) : (
          <LinkedMiniRow
            detail="No operation is linked to this table yet"
            label="Connect refresh evidence"
            state="unknown"
            to="/operations"
          />
        )}
        <MiniRow
          detail={
            preview
              ? `${preview.rows.length} loaded${preview.has_more ? ' · more available' : ''}`
              : 'Preview not loaded'
          }
          label="Preview rows"
        />
      </div>
    )
  }

  return (
    <div className="divide-border -mx-3 divide-y border-y">
      {(preview?.rows ?? []).slice(0, 4).map((row, index) => (
        <div
          className="flex flex-col gap-0.5 px-3 py-2"
          key={String(row._phlo_row_id ?? index)}
        >
          <span className="text-foreground font-mono text-[11px]">
            {String(row._phlo_row_id ?? `row-${index + 1}`)}
          </span>
          <span className="text-muted-foreground truncate font-mono text-[10px]">
            {Object.entries(row)
              .filter(([key]) => key !== '_phlo_row_id')
              .slice(0, 3)
              .map(([key, value]) => `${key}: ${String(value)}`)
              .join(' · ')}
          </span>
        </div>
      ))}
      {(preview?.rows ?? []).length === 0 &&
        (preview?.columns ?? [])
          .slice(0, 6)
          .map((column) => (
            <MiniRow detail="column" key={column} label={column} />
          ))}
      {preview && preview.columns.length === 0 && (
        <p className="text-muted-foreground px-3 py-2 text-xs">
          No column preview available yet.
        </p>
      )}
    </div>
  )
}

function buildTableGraph(
  tables: Array<ObservatoryTable>,
  assets: Array<ObservatoryAsset>,
): {
  nodes: Array<ObservatoryFlowNode>
  edges: Array<ObservatoryFlowEdge>
} {
  const tableByAsset = new Map<string, ObservatoryTable>()
  for (const table of tables) {
    if (table.asset_id) {
      tableByAsset.set(table.asset_id, table)
    }
  }
  const assetById = new Map(assets.map((asset) => [asset.id, asset]))

  const tableNodes = sortTablesForLineage(tables).map(
    (table): ObservatoryFlowNode => ({
      id: table.id,
      label: table.name,
      kind: 'table',
      lane: tableLane(table),
      subtitle: table.namespace ?? table.schema_name,
      metric: table.format ?? 'table',
    }),
  )

  const edges = tables.flatMap((table): Array<ObservatoryFlowEdge> => {
    if (!table.asset_id) return []
    const asset = assetById.get(table.asset_id)
    if (!asset) return []
    const dependencyEdges: Array<ObservatoryFlowEdge> = []
    for (const dependencyId of asset.dependencies) {
      const dependency = tableByAsset.get(dependencyId)
      if (!dependency) continue
      dependencyEdges.push({
        id: `${dependency.id}->${table.id}`,
        source: dependency.id,
        target: table.id,
      })
    }
    return dependencyEdges
  })

  return { nodes: tableNodes, edges }
}

function sortTablesForLineage(
  tables: Array<ObservatoryTable>,
): Array<ObservatoryTable> {
  return tables.slice().sort((left, right) => {
    const leftLane = tableLane(left)
    const rightLane = tableLane(right)
    if (leftLane !== rightLane) {
      return laneRank(leftLane) - laneRank(rightLane)
    }
    return left.name.localeCompare(right.name)
  })
}

function chooseDefaultTable(
  tables: Array<ObservatoryTable>,
): ObservatoryTable | null {
  return (
    tables.find(
      (table) =>
        tableCatalogState(table) === 'Queryable' && tableLane(table) === 'gold',
    ) ??
    tables.find((table) => tableLane(table) === 'gold') ??
    tables.find(
      (table) =>
        tableCatalogState(table) === 'Queryable' &&
        readTableRecordCount(table) !== null,
    ) ??
    tables.find(
      (table) =>
        tableCatalogState(table) === 'Queryable' &&
        tableLane(table) === 'silver',
    ) ??
    tables.find((table) => tableCatalogState(table) === 'Queryable') ??
    tables.find((table) => tableLane(table) === 'silver') ??
    tables[0] ??
    null
  )
}

function readTableRecordCount(
  table: ObservatoryTable,
): string | number | boolean | null {
  return (
    readMetric(table.metadata, 'rows') ??
    readMetric(table.metadata, 'records') ??
    readMetric(table.metadata, 'row_count')
  )
}

function filterTables(
  tables: Array<ObservatoryTable>,
  query: string,
): Array<ObservatoryTable> {
  const needle = query.trim().toLowerCase()
  if (!needle) return tables
  return tables.filter((table) =>
    [
      table.name,
      table.id,
      table.namespace,
      table.schema_name,
      table.format,
      table.branch,
      table.asset_id,
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(needle)),
  )
}

function buildTableSummary(
  tables: Array<ObservatoryTable>,
  datasets: Array<ObservatoryDataset>,
  quality: Array<ObservatoryQualityCheck>,
  selected: ObservatoryTable | null,
): TableSummary {
  return {
    total: tables.length,
    queryable: tables.filter(
      (table) => tableCatalogState(table) === 'Queryable',
    ).length,
    datasetBound: tables.filter((table) => datasetForTable(table, datasets))
      .length,
    qualityLinked: tables.filter(
      (table) => qualityForTable(table, quality).length > 0,
    ).length,
    selectedLabel: selected?.id ?? 'None selected',
    selectedCatalog: selected ? tableCatalogState(selected) : 'Unknown',
  }
}

function tableDatasetLabel(dataset: ObservatoryDataset | null): string {
  if (!dataset) return 'Unbound'
  if (dataset.candidate) return 'Candidate'
  return dataset.name
}

function isTransientPreviewMiss(error: string | null): boolean {
  return error?.toLowerCase().includes('table not found') ?? false
}

function tableCatalogState(table: ObservatoryTable): string {
  const state = readMetric(table.metadata, 'catalog_state')
  if (state === 'queryable') return 'Queryable'
  if (state === 'model_only') return 'Model only'

  const present = table.metadata.catalog_present
  if (present === true) return 'Queryable'
  if (present === false) return 'Model only'

  return 'Unknown'
}

function previewEmptyCopy(table: ObservatoryTable): string {
  if (tableCatalogState(table) === 'Model only') {
    return 'This model is registered, but it is not materialized as a queryable table.'
  }
  return 'Preview rows are unavailable.'
}

function datasetForTable(
  table: ObservatoryTable,
  datasets: Array<ObservatoryDataset>,
): ObservatoryDataset | null {
  return (
    datasets.find((dataset) =>
      dataset.source_refs.some(
        (ref) =>
          (ref.kind === 'table' && ref.id === table.id) ||
          (ref.kind === 'asset' && ref.id === table.asset_id),
      ),
    ) ?? null
  )
}

function qualityForTable(
  table: ObservatoryTable,
  quality: Array<ObservatoryQualityCheck>,
): Array<ObservatoryQualityCheck> {
  if (!table.asset_id) return []
  return quality.filter((check) => check.asset_id === table.asset_id)
}

function operationsForTable(
  table: ObservatoryTable,
  dataset: ObservatoryDataset | null,
  operations: Array<ObservatoryOperation>,
): Array<ObservatoryOperation> {
  return operations
    .filter((operation) => {
      if (dataset && operation.target?.kind === 'dataset') {
        return operation.target.id === dataset.id
      }
      return operationMatchesTable(operation, table, null)
    })
    .sort((left, right) =>
      operationTimestamp(right).localeCompare(operationTimestamp(left)),
    )
}

function defaultSqlForTable(table: ObservatoryTable): string {
  return `select * from ${table.id} limit ${previewLimit}`
}

function buildTableProfile(
  table: ObservatoryTable,
  preview: ObservatoryTablePreview | null,
  assets: Array<ObservatoryAsset>,
  quality: Array<ObservatoryQualityCheck>,
  operations: Array<ObservatoryOperation>,
): TableProfile {
  const asset = table.asset_id
    ? assets.find((candidate) => candidate.id === table.asset_id)
    : null
  const dependencies = new Set(asset?.dependencies ?? [])
  const downstream = assets.filter((candidate) =>
    candidate.dependencies.includes(asset?.id ?? ''),
  )
  const checks = quality.filter((check) => check.asset_id === asset?.id)
  const linkedOperations = operations
    .filter((operation) => operationMatchesTable(operation, table, asset))
    .sort((left, right) =>
      operationTimestamp(right).localeCompare(operationTimestamp(left)),
    )
  const qualityState = checks.some((check) => check.status === 'failing')
    ? 'error'
    : checks.some((check) => check.status === 'warning' || check.blocking)
      ? 'warning'
      : checks.length > 0
        ? 'ok'
        : 'unknown'

  return {
    stage: stageLabelForTable(table, asset),
    records:
      preview?.row_count ??
      readTableRecordCount(table) ??
      readMetric(asset?.metadata ?? {}, 'records') ??
      null,
    columns: preview?.columns.length ?? readNumber(table.metadata.columns),
    upstream: dependencies.size,
    downstream: downstream.length,
    qualityLabel:
      checks.length === 0
        ? 'No checks'
        : `${checks.filter((check) => check.status === 'passing').length}/${checks.length} passing`,
    qualityState,
    latestOperation: linkedOperations[0] ?? null,
    businessKeys: detectBusinessKeys(preview, table),
  }
}

function operationMatchesTable(
  operation: ObservatoryOperation,
  table: ObservatoryTable,
  asset: ObservatoryAsset | null | undefined,
): boolean {
  const haystack = [
    operation.target?.id,
    operation.target?.label,
    operation.kind,
    operation.name,
    ...Object.values(operation.metadata).map((value) => String(value)),
  ]
    .join(' ')
    .toLowerCase()
  return [table.id, table.name, table.asset_id, asset?.id, asset?.name]
    .filter(Boolean)
    .some((value) => haystack.includes(String(value).toLowerCase()))
}

function operationTimestamp(operation: ObservatoryOperation): string {
  return operation.completed_at ?? operation.started_at ?? operation.id
}

function detectBusinessKeys(
  preview: ObservatoryTablePreview | null,
  table: ObservatoryTable,
): Array<string> {
  const columns = preview?.columns ?? []
  const explicit = [
    'experiment_id',
    'export_id',
    'plate_id',
    'assay_type',
    'id',
  ]
  const matches = explicit.filter((key) =>
    columns.some((column) => column.toLowerCase() === key),
  )
  if (matches.length > 0) return matches.slice(0, 4)
  const name = table.name.toLowerCase()
  if (name.includes('release')) return ['release metrics']
  if (name.includes('sample')) return ['sample keys']
  return columns
    .filter((column) => column.toLowerCase().endsWith('_id'))
    .slice(0, 4)
}

function stageLabelForTable(
  table: ObservatoryTable,
  asset: ObservatoryAsset | null | undefined,
) {
  const stage =
    readMetric(table.metadata, 'stage') ??
    readMetric(asset?.metadata ?? {}, 'stage') ??
    tableLane(table)
  return String(stage).charAt(0).toUpperCase() + String(stage).slice(1)
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  if (typeof value === 'string') return value
  if (
    typeof value === 'number' ||
    typeof value === 'boolean' ||
    typeof value === 'bigint'
  ) {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function tableLane(table: ObservatoryTable): string {
  const namespace = (table.namespace ?? '').toLowerCase()
  const name = table.name.toLowerCase()
  if (namespace === 'nightscout' || name.startsWith('dlt_')) return 'raw'
  if (namespace === 'bronze' || name.startsWith('stg_')) return 'bronze'
  if (namespace === 'silver') return 'silver'
  if (namespace === 'gold') return 'gold'
  if (namespace === 'marts' || name.startsWith('mrt_')) return 'marts'
  return 'table'
}

function laneRank(lane: string): number {
  return ['raw', 'bronze', 'silver', 'gold', 'marts', 'table'].indexOf(lane)
}
