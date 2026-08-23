/**
 * /logs route. Log explorer with facet-driven level, source, and query
 * filtering; all view state lives in a single reducer.
 */
import { createFileRoute } from '@tanstack/react-router'
import { AlertCircle, FileText, Radio, Search, Terminal } from 'lucide-react'
import { useCallback, useEffect, useMemo, useReducer } from 'react'

import type {
  ObservatoryLogEvent,
  ObservatoryLogFacets,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import {
  getObservatoryLogFacets,
  getObservatoryLogRecords,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { platformMetadataRows } from '@/observatory/platformMetadata'
import {
  loadCachedResource,
  useLiveResource,
} from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/logs')({
  component: Logs,
})

type LogsState = {
  facets: ObservatoryResourceResult<ObservatoryLogFacets>
  level: string
  query: string
  selectedId: string | null
  source: string
}

type LogsAction =
  | { type: 'facets'; facets: ObservatoryResourceResult<ObservatoryLogFacets> }
  | { type: 'level'; level: string }
  | { type: 'query'; query: string }
  | { type: 'selected'; selectedId: string | null }
  | { type: 'source'; source: string }

function logsReducer(state: LogsState, action: LogsAction): LogsState {
  switch (action.type) {
    case 'facets':
      return { ...state, facets: action.facets }
    case 'level':
      return { ...state, level: action.level }
    case 'query':
      return { ...state, query: action.query }
    case 'selected':
      return { ...state, selectedId: action.selectedId }
    case 'source':
      return { ...state, source: action.source }
  }
}

export function Logs() {
  const result = useLiveResource(
    getObservatoryLogRecords,
    120_000,
    'observatory:logs',
  )
  const rawLogs = result.data ?? []
  const logs = useMemo(() => collapseRepeatedLogs(rawLogs), [rawLogs])
  const isLoading = result.isLoading
  const [{ facets, level, query, selectedId, source }, dispatch] = useReducer(
    logsReducer,
    {
      data: null,
      error: null,
    },
    (initialFacets): LogsState => ({
      facets: initialFacets,
      level: 'all',
      query: '',
      selectedId: null,
      source: 'all',
    }),
  )
  const sources = new Set(logs.map((log) => log.source ?? 'platform'))
  const levels = new Set(logs.map((log) => log.level))
  const filtered = useMemo(
    () =>
      logs.filter(
        (log) =>
          (level === 'all' || log.level === level) &&
          (source === 'all' || (log.source ?? 'platform') === source) &&
          matchesLogQuery(log, query),
      ),
    [level, logs, query, source],
  )
  const selected =
    filtered.find((log) => log.id === selectedId) ?? filtered[0] ?? null
  const summary = useMemo(() => summarizeLogs(logs), [logs])
  const selectLog = useCallback((nextSelectedId: string) => {
    dispatch({ type: 'selected', selectedId: nextSelectedId })
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('logId', nextSelectedId)
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [])

  useEffect(() => {
    void loadCachedResource('observatory:log-facets', getObservatoryLogFacets, {
      staleMs: 120_000,
    }).then((nextFacets) => dispatch({ type: 'facets', facets: nextFacets }))
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get('logId')
    if (!requested || requested === selectedId) return
    if (logs.some((log) => log.id === requested)) {
      dispatch({ type: 'selected', selectedId: requested })
    }
  }, [logs, selectedId])

  return (
    <ObservatoryPage
      kicker="Logs"
      title="Evidence console"
      description="Triage platform events, inspect structured payloads, and jump back to the affected target."
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${sources.size} sources`}
        </span>
      }
    >
      <section className="phlo-observatory-log-shell">
        <div className="phlo-observatory-log-console">
          <div className="phlo-observatory-log-summary">
            <LogSummaryCell
              label="Errors"
              value={isLoading ? 'Loading' : summary.error}
            />
            <LogSummaryCell
              label="Warnings"
              value={isLoading ? 'Loading' : summary.warning}
            />
            <LogSummaryCell
              label="Info"
              value={isLoading ? 'Loading' : summary.info}
            />
            <LogSummaryCell
              label="Sources"
              value={isLoading ? 'Loading' : summary.sources}
            />
            <LogSummaryCell
              label="Linked targets"
              value={isLoading ? 'Loading' : summary.resources}
            />
          </div>
          <div className="phlo-observatory-console-toolbar phlo-observatory-log-toolbar">
            <span className="phlo-observatory-log-toolbar-title">
              <Terminal className="size-4" />
              Event evidence
            </span>
            <span className="phlo-observatory-pill">
              {isLoading
                ? 'Loading'
                : rawLogs.length === logs.length
                  ? `${filtered.length} / ${logs.length} events`
                  : `${filtered.length} / ${logs.length} groups · ${rawLogs.length} events`}
            </span>
          </div>
          <div className="phlo-observatory-filter-row">
            <label className="phlo-observatory-search-field">
              <Search className="size-4" />
              <input
                aria-label="Search logs"
                onChange={(event) =>
                  dispatch({ type: 'query', query: event.target.value })
                }
                placeholder="Search evidence"
                value={query}
              />
            </label>
            <select
              value={source}
              onChange={(event) =>
                dispatch({ type: 'source', source: event.target.value })
              }
            >
              <option value="all">All sources</option>
              {Array.from(sources).map((entry) => (
                <option key={entry} value={entry}>
                  {displayLogSource(entry)}
                </option>
              ))}
            </select>
            <select
              value={level}
              onChange={(event) =>
                dispatch({ type: 'level', level: event.target.value })
              }
            >
              <option value="all">All levels</option>
              {Array.from(levels).map((entry) => (
                <option key={entry} value={entry}>
                  {entry}
                </option>
              ))}
            </select>
          </div>
          <div className="phlo-observatory-console-body">
            <div className="phlo-observatory-log-head" role="row">
              <span>Time</span>
              <span>Level</span>
              <span>Message</span>
              <span>Source</span>
            </div>
            {filtered.map((log) => (
              <LogLine
                key={log.id}
                log={log}
                onSelect={selectLog}
                selected={log.id === selected?.id}
              />
            ))}
            {isLoading ? (
              <div className="phlo-observatory-empty-state">
                Reading live platform event evidence.
              </div>
            ) : (
              filtered.length === 0 && (
                <div className="phlo-observatory-empty-state">
                  No log events match the current filters.
                </div>
              )
            )}
          </div>
        </div>

        <aside className="phlo-observatory-inspector">
          <div className="phlo-observatory-inspector-label">
            Evidence detail
          </div>
          {selected ? (
            <>
              <h2>{displayLogSource(selected.source)}</h2>
              <p>{selected.message}</p>
              <dl className="phlo-observatory-facts">
                <Fact label="Level" value={selected.level} />
                <Fact
                  label="Target"
                  value={selected.resource?.label ?? 'platform'}
                />
                <Fact
                  label="Scope"
                  value={
                    selected.resource
                      ? resourceLabel(selected.resource.kind)
                      : 'event'
                  }
                />
                <Fact
                  label="Timestamp"
                  value={selected.timestamp ?? 'not timestamped'}
                />
              </dl>
              {selected.resource && routeHrefForResource(selected.resource) && (
                <a
                  className="phlo-observatory-linked-resource"
                  href={routeHrefForResource(selected.resource)!}
                >
                  <FileText className="size-3.5" />
                  {resourceActionLabel(selected.resource.kind)}
                </a>
              )}
              <div className="phlo-observatory-detail-list">
                {platformMetadataRows(selected.metadata).map((row) => (
                  <div className="phlo-observatory-mini-row" key={row.label}>
                    <span>{row.label}</span>
                    <small>{row.value}</small>
                  </div>
                ))}
                {platformMetadataRows(selected.metadata).length === 0 && (
                  <div className="phlo-observatory-mini-row">
                    <span>Metadata</span>
                    <small>No structured fields</small>
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <h2>{isLoading ? 'Loading evidence' : 'No events'}</h2>
              <p>
                {isLoading
                  ? 'Reading live platform events and structured log fields.'
                  : 'Logs will appear here as Phlo and stack services emit events.'}
              </p>
            </>
          )}
          <div className="phlo-observatory-detail-list">
            <div className="phlo-observatory-mini-row">
              <span>Facets</span>
              <small>
                {sources.size} sources · {levels.size} levels ·{' '}
                {facets.data?.resources.length ?? 0} targets
              </small>
            </div>
          </div>
          {facets.error && (
            <div className="phlo-observatory-panel-footer">{facets.error}</div>
          )}
          {result.error && (
            <div className="phlo-observatory-panel-footer">{result.error}</div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function routeHrefForResource(
  resource: ObservatoryLogEvent['resource'],
): string | null {
  if (!resource) return null
  if (resource.kind === 'dataset') {
    return `/datasets/${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'asset') {
    return `/lineage?assetId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'table') {
    return `/tables?tableId=${encodeURIComponent(resource.id)}`
  }
  return null
}

function resourceLabel(kind: string): string {
  if (kind === 'asset') return 'lineage'
  return kind
}

function resourceActionLabel(kind: string): string {
  if (kind === 'asset') return 'Open lineage'
  return `Open ${resourceLabel(kind)}`
}

function summarizeLogs(logs: Array<ObservatoryLogEvent>): {
  error: number
  warning: number
  info: number
  sources: number
  resources: number
} {
  const sources = new Set<string>()
  const resources = new Set<string>()
  let error = 0
  let warning = 0
  let info = 0
  for (const log of logs) {
    sources.add(log.source ?? 'platform')
    if (log.resource) resources.add(`${log.resource.kind}:${log.resource.id}`)
    if (log.level === 'error') error += 1
    else if (log.level === 'warning') warning += 1
    else if (log.level === 'info') info += 1
  }
  return {
    error,
    warning,
    info,
    sources: sources.size,
    resources: resources.size,
  }
}

/**
 * Provider discovery can emit the same warning on every capability probe.
 * Keep the newest event addressable while retaining the occurrence count as
 * structured evidence, so operational events are not buried by probe noise.
 */
function collapseRepeatedLogs(
  logs: Array<ObservatoryLogEvent>,
): Array<ObservatoryLogEvent> {
  const groups = new Map<
    string,
    { event: ObservatoryLogEvent; occurrences: number }
  >()

  for (const log of logs) {
    const key = [
      log.level,
      log.source ?? 'platform',
      log.message,
      log.resource?.kind ?? '',
      log.resource?.id ?? '',
    ].join('\u0000')
    const existing = groups.get(key)
    if (existing) {
      existing.occurrences += 1
      continue
    }
    groups.set(key, { event: log, occurrences: 1 })
  }

  return Array.from(groups.values()).map(({ event, occurrences }) =>
    occurrences === 1
      ? event
      : {
          ...event,
          metadata: {
            ...event.metadata,
            occurrences,
            grouping: 'Repeated events collapsed; newest event shown.',
          },
        },
  )
}

function LogSummaryCell({
  label,
  value,
}: {
  label: string
  value: string | number
}) {
  return (
    <div className="phlo-observatory-log-summary-cell">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function matchesLogQuery(log: ObservatoryLogEvent, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return [
    log.message,
    log.level,
    log.source,
    log.resource?.label,
    log.resource?.kind,
  ]
    .filter(Boolean)
    .some((value) => value!.toLowerCase().includes(needle))
}

function LogLine({
  log,
  onSelect,
  selected,
}: {
  log: ObservatoryLogEvent
  onSelect: (id: string) => void
  selected: boolean
}) {
  const Icon =
    log.level === 'error'
      ? AlertCircle
      : log.level === 'info'
        ? Radio
        : FileText

  return (
    <button
      className="phlo-observatory-log-line"
      data-active={selected}
      onClick={() => onSelect(log.id)}
      type="button"
    >
      <span className="phlo-observatory-log-time">
        {log.timestamp ?? '--:--:--'}
      </span>
      <span className="phlo-observatory-log-level" data-level={log.level}>
        <Icon className="size-3.5" />
        {log.level}
      </span>
      <span className="phlo-observatory-log-message">{log.message}</span>
      <span className="phlo-observatory-log-source">
        {displayLogSource(log.source) ?? log.resource?.label ?? 'platform'}
      </span>
    </button>
  )
}

function displayLogSource(source?: string | null): string | null {
  if (!source) return source ?? null
  if (source === 'observatory-fixture') return 'Lakehouse manifest'
  return source.replace(/\bassets\b/gi, 'resources')
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
