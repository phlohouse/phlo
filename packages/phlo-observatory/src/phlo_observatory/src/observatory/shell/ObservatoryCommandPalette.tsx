/**
 * Command palette. Debounced search across nav pages, cached table/service
 * lists, and the observatory search API; selecting a result navigates or
 * copies a table's SQL preview to the clipboard.
 */
import { useNavigate } from '@tanstack/react-router'
import { Command as CommandPrimitive } from 'cmdk'
import {
  Activity,
  Boxes,
  CirclePlay,
  Clipboard,
  Database,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  Logs,
  Search,
  Server,
  Settings,
  UploadCloud,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'

import type {
  ObservatoryCapabilityPage,
  ObservatoryResourceResult,
  ObservatorySearchResult,
  ObservatoryService,
  ObservatoryTable,
} from '@/observatory/api/types'
import {
  getObservatoryServices,
  getObservatoryTableRecords,
  searchObservatory,
  searchObservatoryDirect,
} from '@/observatory/api/resources'
import { loadCachedResource } from '@/observatory/routes/liveResource'

const commandGroupLimit = 6

const iconByPageId: Record<string, typeof LayoutDashboard> = {
  overview: LayoutDashboard,
  services: Server,
  operations: Activity,
  runs: CirclePlay,
  tables: Database,
  lineage: Boxes,
  workflows: Clipboard,
  quality: ListChecks,
  logs: Logs,
  branches: GitBranch,
  extensions: Settings,
  storage: Database,
  observability: Activity,
  governance: Settings,
  datasets: Boxes,
  publishing: UploadCloud,
  pipelines: Activity,
  apis: Server,
  bi: LayoutDashboard,
  settings: Settings,
}

export function ObservatoryCommandPalette({
  navItems,
  onClose,
}: {
  navItems: Array<ObservatoryCapabilityPage>
  onClose: () => void
}) {
  const navigate = useNavigate()
  const overlayRef = useRef<HTMLDivElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<
    ObservatoryResourceResult<Array<ObservatorySearchResult>>
  >({
    data: null,
    error: null,
  })
  const [commandTables, setCommandTables] = useState<
    ObservatoryResourceResult<Array<ObservatoryTable>>
  >({
    data: null,
    error: null,
  })
  const [commandServices, setCommandServices] = useState<
    ObservatoryResourceResult<Array<ObservatoryService>>
  >({
    data: null,
    error: null,
  })

  useEffect(() => {
    const previousFocus = document.activeElement
    const timer = window.setTimeout(() => searchInputRef.current?.focus(), 0)
    return () => {
      window.clearTimeout(timer)
      if (previousFocus instanceof HTMLElement) {
        previousFocus.focus()
      }
    }
  }, [])

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults({ data: null, error: null })
      return
    }
    let cancelled = false
    const timer = window.setTimeout(() => {
      const request =
        typeof window === 'undefined'
          ? searchObservatory({ data: { query } })
          : searchObservatoryDirect({ query })
      void request.then((next) => {
        if (!cancelled) setResults(next)
      })
    }, 180)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query])

  useEffect(() => {
    let cancelled = false
    void Promise.all([
      loadCachedResource('observatory:tables', getObservatoryTableRecords, {
        staleMs: 120_000,
      }),
      loadCachedResource('observatory:services', getObservatoryServices, {
        staleMs: 120_000,
      }),
    ]).then(([tables, services]) => {
      if (cancelled) return
      setCommandTables(tables)
      setCommandServices(services)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const closeSearch = useCallback(() => {
    setQuery('')
    onClose()
  }, [onClose])

  const handleCommandSelect = useCallback(
    (value: string) => {
      if (value.startsWith('copy-sql:')) {
        const tableId = value.replace('copy-sql:', '')
        const sql = `select * from ${tableId} limit 100`
        void navigator.clipboard?.writeText(sql)
        closeSearch()
        return
      }

      const href = exactInternalHref(value.replace('open:', ''))
      closeSearch()
      void navigate({ href })
    },
    [closeSearch, navigate],
  )

  const trapFocus = useCallback((event: KeyboardEvent) => {
    if (event.key !== 'Tab') return

    const focusable = Array.from(
      overlayRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    ).filter(
      (element) =>
        !element.hasAttribute('disabled') &&
        element.getAttribute('aria-hidden') !== 'true',
    )
    if (focusable.length === 0) return

    const first = focusable[0]
    const last = focusable.at(-1)
    if (!last) return

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }, [])

  const groupedResults = useMemo(() => {
    const groups = new Map<string, Array<ObservatorySearchResult>>()
    const excludedKinds = new Set(['service', 'table', 'dataset'])
    for (const result of results.data ?? []) {
      if (excludedKinds.has(result.kind)) continue
      const key = result.kind || 'result'
      const group = groups.get(key)
      if (group) {
        group.push(result)
      } else {
        groups.set(key, [result])
      }
    }
    return Array.from(groups.entries())
  }, [results.data])

  const tableResults = useMemo(
    () => (results.data ?? []).filter((result) => result.kind === 'table'),
    [results.data],
  )
  const datasetResults = useMemo(
    () => (results.data ?? []).filter((result) => result.kind === 'dataset'),
    [results.data],
  )

  const tableMatches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const tables = commandTables.data ?? []
    if (!needle) return tables.slice(0, 12)

    return tables
      .filter((table) =>
        [
          table.id,
          table.name,
          tableLabel(table),
          table.namespace,
          table.schema_name,
          table.branch,
          table.format,
          table.asset_id,
          table.metadata.table,
          table.metadata.table_name,
          table.metadata.schema,
          table.metadata.database,
          table.metadata.catalog,
          table.metadata.relation,
          table.metadata.materialized,
        ]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(needle)),
      )
      .slice(0, 16)
  }, [commandTables.data, query])

  const serviceMatches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const services = commandServices.data ?? []
    if (!needle) return services.slice(0, 12)

    return services
      .filter((service) =>
        [
          service.id,
          service.name,
          service.kind,
          service.status,
          service.profile,
          service.backend,
          service.health.state,
          service.health.message,
          service.metadata.description,
        ]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(needle)),
      )
      .slice(0, 16)
  }, [commandServices.data, query])

  const pageMatches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return navItems
    return navItems.filter((item) =>
      [item.id, item.label, item.path, ...item.providers]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle)),
    )
  }, [navItems, query])

  const hasLocalMatches =
    pageMatches.length > 0 ||
    tableMatches.length > 0 ||
    datasetResults.length > 0 ||
    serviceMatches.length > 0
  const sqlTemplateTargets = tableMatches.length
    ? tableMatches
    : tableResults.slice(0, 6).flatMap((result) => {
        const table = tableFromSearchResult(result)
        return table ? [table] : []
      })

  return (
    <div
      aria-label="Command search"
      aria-modal="true"
      className="phlo-observatory-command-overlay"
      onKeyDown={trapFocus}
      ref={overlayRef}
      role="dialog"
    >
      <button
        aria-label="Close search"
        className="phlo-observatory-command-backdrop"
        onClick={closeSearch}
        type="button"
      />
      <div className="phlo-observatory-search-popover">
        <CommandPrimitive
          className="phlo-observatory-command phlo-observatory-command-palette"
          loop
          shouldFilter={false}
        >
          <div className="phlo-observatory-search-field">
            <Search className="size-4" />
            <CommandPrimitive.Input
              aria-label="Search Observatory"
              id="phlo-observatory-command-input"
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  event.preventDefault()
                  closeSearch()
                }
              }}
              onValueChange={setQuery}
              placeholder="Search services, lineage, datasets, tables, checks"
              ref={searchInputRef}
              value={query}
            />
            <kbd>Esc</kbd>
          </div>
          <CommandPrimitive.List className="phlo-observatory-command-list">
            {query.trim().length >= 2 && pageMatches.length > 0 && (
              <CommandPrimitive.Group
                className="phlo-observatory-command-group"
                heading={`Pages (${pageMatches.length})`}
              >
                {pageMatches.slice(0, commandGroupLimit).map((item) => {
                  const Icon = iconByPageId[item.id] ?? LayoutDashboard
                  return (
                    <CommandPrimitive.Item
                      className="phlo-observatory-command-item"
                      key={`nav:${item.id}`}
                      onSelect={handleCommandSelect}
                      value={`open:${item.path}`}
                    >
                      <Icon className="size-4" />
                      <span>{item.label}</span>
                      <small>{item.providers.join(', ') || 'core'}</small>
                    </CommandPrimitive.Item>
                  )
                })}
              </CommandPrimitive.Group>
            )}

            {query.trim().length < 2 && (
              <CommandPrimitive.Group
                className="phlo-observatory-command-group"
                heading="Fast actions"
              >
                <CommandPrimitive.Item
                  className="phlo-observatory-command-item"
                  onSelect={handleCommandSelect}
                  value="open:/datasets"
                >
                  <Boxes className="size-4" />
                  <span>Browse Datasets</span>
                  <small>Readiness and ownership</small>
                </CommandPrimitive.Item>
                <CommandPrimitive.Item
                  className="phlo-observatory-command-item"
                  onSelect={handleCommandSelect}
                  value="open:/lineage"
                >
                  <Boxes className="size-4" />
                  <span>Inspect lineage and impact</span>
                  <small>Lineage</small>
                </CommandPrimitive.Item>
                <CommandPrimitive.Item
                  className="phlo-observatory-command-item"
                  onSelect={handleCommandSelect}
                  value="open:/runs"
                >
                  <CirclePlay className="size-4" />
                  <span>Review orchestration runs</span>
                  <small>Runs</small>
                </CommandPrimitive.Item>
              </CommandPrimitive.Group>
            )}

            {query.trim().length >= 2 && tableMatches.length > 0 && (
              <CommandPrimitive.Group
                className="phlo-observatory-command-group"
                heading={`Tables (${tableMatches.length})`}
              >
                {tableMatches.slice(0, commandGroupLimit).map((table) => (
                  <CommandPrimitive.Item
                    className="phlo-observatory-command-item"
                    key={`table:${table.id}`}
                    onSelect={handleCommandSelect}
                    value={`open:/tables?tableId=${encodeURIComponent(table.id)}`}
                  >
                    <Database className="size-4" />
                    <span>{tableLabel(table)}</span>
                    <small>{table.branch ?? table.format ?? 'table'}</small>
                  </CommandPrimitive.Item>
                ))}
                {tableMatches.length > commandGroupLimit && (
                  <CommandPrimitive.Item
                    className="phlo-observatory-command-item"
                    onSelect={handleCommandSelect}
                    value="open:/tables"
                  >
                    <Database className="size-4" />
                    <span>Open table browser</span>
                    <small>{tableMatches.length} matches</small>
                  </CommandPrimitive.Item>
                )}
              </CommandPrimitive.Group>
            )}

            {query.trim().length >= 2 && datasetResults.length > 0 && (
              <CommandPrimitive.Group
                className="phlo-observatory-command-group"
                heading={`Datasets (${datasetResults.length})`}
              >
                {datasetResults.slice(0, commandGroupLimit).map((result) => (
                  <CommandPrimitive.Item
                    className="phlo-observatory-command-item"
                    key={result.id}
                    onSelect={handleCommandSelect}
                    value={`open:${datasetHrefFromSearchResult(result)}`}
                  >
                    <Boxes className="size-4" />
                    <span>{result.label}</span>
                    <small>{result.summary ?? 'Dataset readiness'}</small>
                  </CommandPrimitive.Item>
                ))}
              </CommandPrimitive.Group>
            )}

            {query.trim().length >= 2 && serviceMatches.length > 0 && (
              <CommandPrimitive.Group
                className="phlo-observatory-command-group"
                heading={`Services (${serviceMatches.length})`}
              >
                {serviceMatches.slice(0, commandGroupLimit).map((service) => (
                  <CommandPrimitive.Item
                    className="phlo-observatory-command-item"
                    key={`service:${service.id}`}
                    onSelect={handleCommandSelect}
                    value={`open:/services?serviceId=${encodeURIComponent(service.id)}`}
                  >
                    <Server className="size-4" />
                    <span>{service.name}</span>
                    <small>
                      {[service.kind, service.status]
                        .filter(Boolean)
                        .join(' · ')}
                    </small>
                  </CommandPrimitive.Item>
                ))}
                {serviceMatches.length > commandGroupLimit && (
                  <CommandPrimitive.Item
                    className="phlo-observatory-command-item"
                    onSelect={handleCommandSelect}
                    value="open:/services"
                  >
                    <Server className="size-4" />
                    <span>Open service directory</span>
                    <small>{serviceMatches.length} matches</small>
                  </CommandPrimitive.Item>
                )}
              </CommandPrimitive.Group>
            )}

            {groupedResults.map(([kind, items]) => (
              <CommandPrimitive.Group
                className="phlo-observatory-command-group"
                heading={`${commandHeading(kind)} (${items.length})`}
                key={kind}
              >
                {items.slice(0, commandGroupLimit).map((result) => (
                  <CommandPrimitive.Item
                    className="phlo-observatory-command-item"
                    key={result.id}
                    onSelect={handleCommandSelect}
                    value={`open:${exactInternalHref(result.href ?? '/')}`}
                  >
                    {iconForSearchKind(result.kind)}
                    <span>{result.label}</span>
                    <small>{result.summary ?? result.kind}</small>
                  </CommandPrimitive.Item>
                ))}
              </CommandPrimitive.Group>
            ))}

            {query.trim().length >= 2 && sqlTemplateTargets.length > 0 && (
              <CommandPrimitive.Group
                className="phlo-observatory-command-group"
                heading="SQL templates"
              >
                {sqlTemplateTargets.slice(0, 4).map((table) => (
                  <CommandPrimitive.Item
                    className="phlo-observatory-command-item"
                    key={`sql:${table.id}`}
                    onSelect={handleCommandSelect}
                    value={`copy-sql:${table.id}`}
                  >
                    <Clipboard className="size-4" />
                    <span>Copy SELECT from {tableLabel(table)}</span>
                    <small>Read-only template</small>
                  </CommandPrimitive.Item>
                ))}
              </CommandPrimitive.Group>
            )}

            {query.trim().length >= 2 &&
              results.data?.length === 0 &&
              !hasLocalMatches && (
                <CommandPrimitive.Empty className="phlo-observatory-command-empty">
                  No results found.
                </CommandPrimitive.Empty>
              )}
            {results.error && (
              <div className="phlo-observatory-command-empty">
                {results.error}
              </div>
            )}
            {query.trim().length >= 2 && (
              <CommandPrimitive.Group
                className="phlo-observatory-command-group phlo-observatory-command-search-all"
                heading="Search"
              >
                <CommandPrimitive.Item
                  className="phlo-observatory-command-item"
                  onSelect={handleCommandSelect}
                  value={`open:/search?q=${encodeURIComponent(query.trim())}`}
                >
                  <Search className="size-4" />
                  <span>View all results</span>
                  <small>Filter by type and owner</small>
                </CommandPrimitive.Item>
              </CommandPrimitive.Group>
            )}
          </CommandPrimitive.List>
          <div className="phlo-observatory-command-footer">
            <span>↑↓ navigate</span>
            <span>↵ select</span>
            <span>esc close</span>
          </div>
        </CommandPrimitive>
      </div>
    </div>
  )
}

function commandHeading(kind: string): string {
  const labels: Record<string, string> = {
    asset: 'Source bindings',
    table: 'Tables',
    service: 'Services',
    log: 'Logs',
    operation: 'Operations',
    branch: 'Branches',
    extension: 'Extensions',
    run: 'Runs',
    quality: 'Quality',
    setting: 'Settings',
  }
  return labels[kind] ?? kind.replace(/[_-]+/g, ' ')
}

function iconForSearchKind(kind: string): ReactNode {
  if (kind === 'asset') return <Boxes className="size-4" />
  if (kind === 'table') return <Database className="size-4" />
  if (kind === 'service') return <Server className="size-4" />
  if (kind === 'log') return <Logs className="size-4" />
  if (kind === 'branch') return <GitBranch className="size-4" />
  if (kind === 'run') return <CirclePlay className="size-4" />
  return <Search className="size-4" />
}

function tableLabel(table: ObservatoryTable): string {
  const namespace = table.namespace ?? table.schema_name
  if (!namespace) return table.name
  return `${namespace}.${table.name}`
}

function tableFromSearchResult(
  result: ObservatorySearchResult,
): ObservatoryTable | null {
  if (result.kind !== 'table') return null
  const id = result.id.replace(/^table:/, '')
  const labelParts = result.label.split('.')
  const name = labelParts.at(-1) ?? id
  const namespace =
    labelParts.length > 1 ? labelParts.slice(0, -1).join('.') : undefined
  return {
    id,
    name,
    namespace,
    schema_name: namespace,
    branch: undefined,
    format: undefined,
    asset_id: undefined,
    metadata: {},
  }
}

function datasetHrefFromSearchResult(result: ObservatorySearchResult): string {
  if (result.href) return exactInternalHref(result.href)
  return `/datasets/${encodeURIComponent(result.id.replace(/^dataset:/, ''))}`
}

function exactInternalHref(href: string): string {
  if (typeof window === 'undefined') return href || '/'
  const url = new URL(href || '/', window.location.origin)
  if (url.origin !== window.location.origin) return '/'
  return `${url.pathname}${url.search}${url.hash}`
}
