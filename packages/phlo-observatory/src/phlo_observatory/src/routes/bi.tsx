/**
 * /bi route. BI surfaces joined with table records; when nothing is
 * selected it falls back to the Trino-backed surface.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { BarChart3, Database, Send, Table2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatorySurfaceItem,
  ObservatoryTable,
} from '@/observatory/api/types'
import {
  getObservatoryBiItems,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  formatPlatformMetadata,
  metadataDisplayText,
  platformMetadataRows,
  rawMetadataText,
} from '@/observatory/platformMetadata'

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
    <ObservatoryPage
      kicker="BI"
      title="Consumer surfaces"
      description="Serving targets and query engines available to dashboards, reports, and downstream analytics."
      action={
        <span className="phlo-observatory-pill">
          {refreshState ? `${refreshState} · ` : ''}
          {surfaces.length} surfaces
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell phlo-observatory-bi-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-platform-summary">
            <PlatformMetric
              icon={<Send className="size-4" />}
              label="Publish targets"
              value={summary.publishTargets}
            />
            <PlatformMetric
              icon={<Database className="size-4" />}
              label="Query engines"
              value={summary.queryEngines}
            />
            <PlatformMetric
              icon={<Table2 className="size-4" />}
              label="Queryable tables"
              value={`${summary.queryableTables}/${summary.tables}`}
            />
            <PlatformMetric
              icon={<BarChart3 className="size-4" />}
              label="Serving systems"
              value={summary.servingSystems}
            />
          </div>
          <div className="phlo-observatory-browser-toolbar">
            <span>
              <BarChart3 className="size-4" />
              Consumer endpoints
            </span>
            <span className="phlo-observatory-pill">
              {refreshState ? `${refreshState} · ` : ''}
              {surfaces.length} registered
            </span>
          </div>
          <div className="phlo-observatory-platform-table" role="table">
            <div className="phlo-observatory-platform-head" role="row">
              <span>Surface</span>
              <span>Role</span>
              <span>System</span>
              <span>Connection</span>
              <span>State</span>
            </div>
            {surfaces.map((surface) => (
              <BiRow
                key={surface.id}
                onSelect={() => selectSurface(surface.id)}
                selected={surface.id === selected?.id}
                surface={surface}
              />
            ))}
            {isInitialLoading ? (
              <div className="phlo-observatory-run-provider-empty">
                <div>
                  <span className="phlo-observatory-inspector-label">
                    Consumer surfaces
                  </span>
                  <h2>Loading consumer surfaces</h2>
                  <p>Reading live publish targets and query engines.</p>
                </div>
              </div>
            ) : (
              surfaces.length === 0 && (
                <div className="phlo-observatory-run-provider-empty">
                  <div>
                    <span className="phlo-observatory-inspector-label">
                      Consumer surfaces
                    </span>
                    <h2>No BI surfaces configured</h2>
                    <p>
                      The active stack has no publish targets or query-engine
                      consumer records to inspect.
                    </p>
                  </div>
                </div>
              )
            )}
          </div>
        </div>

        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">
            Consumer detail
          </div>
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <p>{selected.summary ?? 'No consumer summary available.'}</p>
              <dl className="phlo-observatory-facts">
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
              </dl>
              <div className="phlo-observatory-detail-list">
                <Link
                  className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
                  to="/tables"
                >
                  <span>Queryable tables</span>
                  <small>{`${summary.queryableTables}/${summary.tables} available`}</small>
                </Link>
                <div className="phlo-observatory-mini-row">
                  <span>Connection</span>
                  <small>{connectionLabel(selected)}</small>
                </div>
                <div className="phlo-observatory-mini-row">
                  <span>Compatibility</span>
                  <small>{compatibilityLabel(selected)}</small>
                </div>
              </div>
              <div className="phlo-observatory-detail-list">
                {platformMetadataRows(selected.metadata).map((row) => (
                  <div className="phlo-observatory-mini-row" key={row.label}>
                    <span>{row.label}</span>
                    <small>{row.value}</small>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <h2>
                {isInitialLoading
                  ? 'Checking consumer detail'
                  : 'No consumer selected'}
              </h2>
              <p>
                {isLoading
                  ? 'Reading live query and publishing context.'
                  : 'Select a consumer surface to inspect query and publishing context.'}
              </p>
            </>
          )}
          {result.error && (
            <div className="phlo-observatory-panel-footer">{result.error}</div>
          )}
          {tablesResult.error && (
            <div className="phlo-observatory-panel-footer">
              {tablesResult.error}
            </div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
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
      className="phlo-observatory-platform-row"
      data-active={selected}
      onClick={onSelect}
      role="row"
      type="button"
    >
      <span>{surface.name}</span>
      <span>{metadataDisplayText(surface, 'capability_type')}</span>
      <span>{surfaceSystem(surface)}</span>
      <span>{connectionLabel(surface)}</span>
      <span>{surface.health.message ?? surface.health.state}</span>
    </button>
  )
}

function PlatformMetric({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: string | number
}) {
  return (
    <div className="phlo-observatory-platform-summary-cell">
      <span>
        {icon}
        {label}
      </span>
      <strong>{value}</strong>
    </div>
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

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
