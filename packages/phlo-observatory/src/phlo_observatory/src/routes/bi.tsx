/**
 * /bi route. BI surfaces joined with table records; when nothing is
 * selected it falls back to the Trino-backed surface.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { BarChart3, Database, Send, Table2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ObservatorySurfaceItem,
  ObservatoryTable,
} from '@/observatory/api/types'
import {
  getObservatoryBiItems,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  formatPlatformMetadata,
  metadataDisplayText,
  platformMetadataRows,
  rawMetadataText,
} from '@/observatory/platformMetadata'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { StatusBadge } from '@/components/observatory/status'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/bi')({
  component: BI,
})

export function BI() {
  const result = useLiveResource(
    getObservatoryBiItems,
    120_000,
    'observatory:bi',
  )
  const tablesResult = useLiveResource(
    getObservatoryTableRecords,
    120_000,
    'observatory:tables',
  )
  const surfaces = result.data ?? []
  const tables = tablesResult.data ?? []
  const isLoading = result.isLoading || tablesResult.isLoading
  const isInitialLoading = result.isLoading && surfaces.length === 0
  const refreshState = isLoading
    ? isInitialLoading
      ? 'checking'
      : 'refreshing'
    : null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected =
    surfaces.find((surface) => surface.id === selectedId) ??
    surfaces.find(
      (surface) => rawMetadataText(surface, 'provider') === 'trino',
    ) ??
    surfaces[0] ??
    null
  const selectSurface = useCallback((surfaceId: string) => {
    setSelectedId(surfaceId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('surfaceId', surfaceId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])
  const summary = useMemo(
    () => summarizeBi(surfaces, tables),
    [surfaces, tables],
  )

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'surfaceId',
    )
    if (!requested || requested === selectedId) return
    if (surfaces.some((surface) => surface.id === requested)) {
      setSelectedId(requested)
    }
  }, [selectedId, surfaces])

  useEffect(() => {
    if (selectedId !== null || !selected) return
    setSelectedId(selected.id)
  }, [selected, selectedId])

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {refreshState ? `${refreshState} · ` : ''}
            {surfaces.length} surfaces
          </Badge>
        }
        description="Serving targets and query engines available to dashboards, reports, and downstream analytics."
        title="Consumer surfaces"
      />
      <StatGrid>
        <StatCard
          icon={<Send className="size-3.5" />}
          label="Publish targets"
          value={summary.publishTargets}
        />
        <StatCard
          icon={<Database className="size-3.5" />}
          label="Query engines"
          value={summary.queryEngines}
        />
        <StatCard
          icon={<Table2 className="size-3.5" />}
          label="Queryable tables"
          value={`${summary.queryableTables}/${summary.tables}`}
        />
        <StatCard
          icon={<BarChart3 className="size-3.5" />}
          label="Serving systems"
          value={summary.servingSystems}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection label="Consumer detail">
              {selected ? (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-foreground text-xs font-semibold">
                      {selected.name}
                    </span>
                    <StatusBadge state={selected.health.state} />
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs/relaxed">
                    {selected.summary ?? 'No consumer summary available.'}
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground text-xs">
                  {isLoading
                    ? 'Reading live query and publishing context.'
                    : 'Select a consumer surface to inspect query and publishing context.'}
                </p>
              )}
            </InspectorSection>
            {selected && (
              <InspectorSection label="Facts">
                <FactGrid>
                  <Fact label="Health" value={selected.health.state} />
                  <Fact
                    label="Provider"
                    value={metadataDisplayText(selected, 'provider')}
                  />
                  <Fact
                    label="Capability"
                    value={metadataDisplayText(selected, 'capability_type')}
                  />
                  <Fact label="System" value={surfaceSystem(selected)} />
                  <Fact label="Connection" value={connectionLabel(selected)} />
                  <Fact
                    label="Compatibility"
                    value={compatibilityLabel(selected)}
                  />
                </FactGrid>
              </InspectorSection>
            )}
            {selected && (
              <InspectorSection label="Coverage">
                <div className="divide-y divide-border border-y">
                  <Link
                    className="hover:bg-accent/50 flex items-center justify-between gap-2 py-2 text-xs transition-colors"
                    to="/tables"
                  >
                    <span className="text-foreground">Queryable tables</span>
                    <span className="text-muted-foreground font-mono text-[10px]">
                      {summary.queryableTables}/{summary.tables} available
                    </span>
                  </Link>
                </div>
              </InspectorSection>
            )}
            {selected && (
              <InspectorSection label="Metadata">
                {platformMetadataRows(selected.metadata).length ? (
                  <FactGrid>
                    {platformMetadataRows(selected.metadata).map((row) => (
                      <Fact
                        key={row.label}
                        label={row.label}
                        value={row.value}
                      />
                    ))}
                  </FactGrid>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    No structured fields.
                  </p>
                )}
                {(result.error ?? tablesResult.error) && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    {result.error ?? tablesResult.error}
                  </p>
                )}
              </InspectorSection>
            )}
          </>
        }
        inspectorWidth="w-[24rem]"
        list={
          <SectionCard className="ring-0" title="Consumer endpoints">
            <div className="text-muted-foreground grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.9fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
              <span>Surface</span>
              <span>Role</span>
              <span>System</span>
              <span>Connection</span>
              <span>State</span>
            </div>
            {isInitialLoading ? (
              <LoadingBlock className="p-3" label="Loading consumer surfaces" />
            ) : surfaces.length === 0 ? (
              <EmptyBlock
                description="The active stack has no publish targets or query-engine consumer records to inspect."
                title="No BI surfaces configured"
              />
            ) : (
              <div className="divide-y divide-border">
                {surfaces.map((surface) => (
                  <BiRow
                    key={surface.id}
                    onSelect={() => selectSurface(surface.id)}
                    selected={surface.id === selected?.id}
                    surface={surface}
                  />
                ))}
              </div>
            )}
          </SectionCard>
        }
      />
    </Page>
  )
}

function BiRow({
  onSelect,
  selected,
  surface,
}: {
  onSelect: () => void
  selected: boolean
  surface: ObservatorySurfaceItem
}) {
  return (
    <button
      className={cn(
        'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1.2fr)_minmax(0,0.9fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1fr)] items-center gap-3 px-3 py-2 text-left transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      onClick={onSelect}
      type="button"
    >
      <span className="text-foreground truncate text-xs font-medium">
        {surface.name}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {metadataDisplayText(surface, 'capability_type')}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {surfaceSystem(surface)}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {connectionLabel(surface)}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {surface.health.message ?? surface.health.state}
      </span>
    </button>
  )
}

function summarizeBi(
  surfaces: Array<ObservatorySurfaceItem>,
  tables: Array<ObservatoryTable>,
): {
  publishTargets: number
  queryEngines: number
  tables: number
  queryableTables: number
  servingSystems: number
} {
  return {
    publishTargets: surfaces.filter(
      (surface) =>
        rawMetadataText(surface, 'capability_type') === 'publish_target',
    ).length,
    queryEngines: surfaces.filter(
      (surface) =>
        rawMetadataText(surface, 'capability_type') === 'query_engine',
    ).length,
    tables: tables.length,
    queryableTables: tables.filter(isQueryableTable).length,
    servingSystems: new Set(
      surfaces.map(surfaceSystem).filter((system) => system !== 'not reported'),
    ).size,
  }
}

function isQueryableTable(table: ObservatoryTable): boolean {
  const state = table.metadata.catalog_state
  if (state === 'queryable') return true
  if (state === 'model_only') return false
  return table.metadata.catalog_present === true
}

function surfaceSystem(surface: ObservatorySurfaceItem): string {
  for (const key of ['target_system', 'service_type', 'provider']) {
    const value = rawMetadataText(surface, key)
    if (value !== 'not reported') return formatPlatformMetadata(value)
  }
  return 'not reported'
}

function connectionLabel(surface: ObservatorySurfaceItem): string {
  const host = rawMetadataText(surface, 'host')
  const port = surface.metadata.port
  if (
    host !== 'not reported' &&
    (typeof port === 'number' || typeof port === 'string')
  ) {
    return `${host}:${port}`
  }
  const database = rawMetadataText(surface, 'default_database')
  if (database !== 'not reported') return formatPlatformMetadata(database)
  const role = rawMetadataText(surface, 'role')
  return role !== 'not reported' ? formatPlatformMetadata(role) : 'not reported'
}

function compatibilityLabel(surface: ObservatorySurfaceItem): string {
  const compatibility = surface.metadata.compatibility
  if (
    typeof compatibility === 'object' &&
    compatibility !== null &&
    'target' in compatibility
  ) {
    return formatPlatformMetadata(compatibility.target)
  }
  return 'not reported'
}
