/**
 * /queries route. Read-only SQL console with tabbed scratch queries, saved
 * queries, and execution history persisted to browser-local activity state.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Database, History, Play, Plus, Save, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type {
  ObservatoryQueryResult,
  ObservatorySavedQuery,
} from '@/observatory/api/types'
import type { ObservatoryQueryWorkspace } from '@/observatory/shell/localActivity'
import {
  getObservatorySavedQueries,
  getObservatoryTableRecords,
  runObservatoryQuery,
  saveObservatoryQuery,
} from '@/observatory/api/resources'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  readQueryWorkspace,
  recordQueryExecution,
  writeQueryWorkspace,
} from '@/observatory/shell/localActivity'
import { Page, PageHeader } from '@/components/observatory/page'
import { EmptyBlock } from '@/components/observatory/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/queries')({ component: Queries })

export function Queries() {
  const tables = useLiveResource(
    getObservatoryTableRecords,
    60_000,
    'observatory:tables',
  )
  const [savedQueries, setSavedQueries] = useState<
    Array<ObservatorySavedQuery>
  >([])
  const [workspace, setWorkspace] = useState<ObservatoryQueryWorkspace>({
    activeId: 'scratch-1',
    tabs: [{ id: 'scratch-1', name: 'Untitled query', sql: '' }],
  })
  const activeTab =
    workspace.tabs.find((tab) => tab.id === workspace.activeId) ??
    workspace.tabs[0]
  const sql = activeTab?.sql ?? ''
  const [name, setName] = useState('')
  const [result, setResult] = useState<ObservatoryQueryResult | null>(null)
  const [message, setMessage] = useState(
    'Select a table or enter a read-only query.',
  )
  const [running, setRunning] = useState(false)
  const hasAppliedSuggestedSql = useRef(false)
  const suggestedSql = useMemo(() => {
    const table = tables.data?.[0]
    return table ? `SELECT * FROM ${table.id} LIMIT 100` : ''
  }, [tables.data])

  useEffect(() => {
    setWorkspace(readQueryWorkspace())
    void getObservatorySavedQueries().then((next) =>
      setSavedQueries(next.data ?? []),
    )
  }, [])
  useEffect(() => {
    if (!suggestedSql || hasAppliedSuggestedSql.current) return
    hasAppliedSuggestedSql.current = true
    setWorkspace((current) => ({
      ...current,
      tabs: current.tabs.map((tab) =>
        tab.id === current.activeId && !tab.sql
          ? { ...tab, sql: suggestedSql }
          : tab,
      ),
    }))
  }, [suggestedSql])

  useEffect(() => writeQueryWorkspace(workspace), [workspace])

  const updateActiveTab = (patch: { name?: string; sql?: string }) => {
    setWorkspace((current) => ({
      ...current,
      tabs: current.tabs.map((tab) =>
        tab.id === current.activeId ? { ...tab, ...patch } : tab,
      ),
    }))
  }

  const openSavedQuery = (query: ObservatorySavedQuery) => {
    const id = `saved:${query.id}`
    setWorkspace((current) => ({
      activeId: id,
      tabs: current.tabs.some((tab) => tab.id === id)
        ? current.tabs.map((tab) =>
            tab.id === id
              ? {
                  ...tab,
                  name: query.name,
                  savedQueryId: query.id,
                  sql: query.sql,
                }
              : tab,
          )
        : [
            ...current.tabs,
            { id, name: query.name, savedQueryId: query.id, sql: query.sql },
          ],
    }))
    setName(query.name)
  }

  const newScratch = () => {
    const id = `scratch-${Date.now()}`
    setWorkspace((current) => ({
      activeId: id,
      tabs: [...current.tabs, { id, name: 'Untitled query', sql: '' }],
    }))
    setName('')
    setResult(null)
  }

  const closeTab = (id: string) => {
    setWorkspace((current) => {
      if (current.tabs.length === 1) return current
      const index = current.tabs.findIndex((tab) => tab.id === id)
      const tabs = current.tabs.filter((tab) => tab.id !== id)
      const activeId =
        current.activeId === id
          ? (tabs[Math.max(0, index - 1)]?.id ?? tabs[0].id)
          : current.activeId
      return { activeId, tabs }
    })
  }

  const runQuery = async () => {
    if (!sql.trim() || running) return
    const started = Date.now()
    setRunning(true)
    setMessage('Running read-only query…')
    const next = await runObservatoryQuery({ data: { sql, limit: 100 } })
    const durationMs = Date.now() - started
    setRunning(false)
    setResult(next.data)
    setMessage(next.error ?? `${next.data?.rows.length ?? 0} rows returned`)
    recordQueryExecution({
      id: `query-run-${started}`,
      sql: sql.trim(),
      status: next.data ? 'succeeded' : 'failed',
      startedAt: new Date(started).toISOString(),
      durationMs,
      rowCount: next.data?.rows.length ?? 0,
      error: next.error ?? undefined,
    })
  }

  const saveQuery = async () => {
    if (!name.trim() || !sql.trim()) return
    const next = await saveObservatoryQuery({
      data: { name: name.trim(), sql },
    })
    if (next.data) {
      setSavedQueries((current) => [
        next.data!,
        ...current.filter((item) => item.id !== next.data?.id),
      ])
      setName('')
      updateActiveTab({ name: next.data.name })
      setMessage(`Saved ${next.data.name}`)
    } else {
      setMessage(next.error ?? 'Query could not be saved')
    }
  }

  return (
    <Page>
      <PageHeader
        actions={
          <>
            <Badge variant="secondary">{savedQueries.length} saved</Badge>
            <Badge variant="secondary">read-only</Badge>
            <Link
              className={cn(
                'border-input hover:bg-accent inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors',
              )}
              to="/query-history"
            >
              <History className="size-3.5" />
              History
            </Link>
          </>
        }
        description="Read-only SQL workbench backed by the active query provider, with project-persisted saved queries."
        title="Query workbench"
      />
      <div className="ring-foreground/10 grid grid-cols-1 gap-0 overflow-hidden rounded-md ring-1 lg:grid-cols-[16rem_minmax(0,1fr)]">
        {/* Saved query library */}
        <aside className="bg-card flex min-h-0 flex-col border-b lg:border-r lg:border-b-0">
          <div className="flex items-center justify-between border-b px-3 py-2">
            <span className="text-muted-foreground flex items-center gap-1.5 text-[10px] font-medium tracking-widest uppercase">
              <Save className="size-3.5" />
              Saved queries
            </span>
            <Button onClick={newScratch} size="xs" variant="ghost">
              <Plus className="size-3.5" />
              New
            </Button>
          </div>
          <ScrollArea className="max-h-72 lg:max-h-none lg:flex-1">
            <div className="divide-y divide-border">
              {savedQueries.map((query) => (
                <button
                  className="hover:bg-accent/50 flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors"
                  key={query.id}
                  onClick={() => openSavedQuery(query)}
                  type="button"
                >
                  <span className="text-foreground truncate text-xs">
                    {query.name}
                  </span>
                  <span className="text-muted-foreground font-mono text-[10px]">
                    {query.branch ?? 'main'}
                  </span>
                </button>
              ))}
              {!savedQueries.length && (
                <p className="text-muted-foreground px-3 py-4 text-[11px]">
                  No saved queries. Save the editor contents to create one.
                </p>
              )}
            </div>
          </ScrollArea>
        </aside>

        {/* Editor surface */}
        <div className="bg-card flex min-w-0 flex-col">
          <div
            className="border-border flex items-center overflow-x-auto border-b"
            role="tablist"
          >
            {workspace.tabs.map((tab) => (
              <button
                aria-selected={tab.id === workspace.activeId}
                className={cn(
                  'border-border flex h-8 flex-none items-center gap-2 border-r px-3 text-xs transition-colors',
                  tab.id === workspace.activeId
                    ? 'bg-background text-foreground'
                    : 'text-muted-foreground hover:text-foreground',
                )}
                key={tab.id}
                onClick={() =>
                  setWorkspace((current) => ({ ...current, activeId: tab.id }))
                }
                role="tab"
                type="button"
              >
                <span className="max-w-36 truncate">{tab.name}</span>
                {workspace.tabs.length > 1 && (
                  <X
                    aria-label={`Close ${tab.name}`}
                    className="text-muted-foreground hover:text-foreground size-3"
                    onClick={(event) => {
                      event.stopPropagation()
                      closeTab(tab.id)
                    }}
                  />
                )}
              </button>
            ))}
            <button
              aria-label="New query tab"
              className="text-muted-foreground hover:text-foreground flex h-8 flex-none items-center px-3 transition-colors"
              onClick={newScratch}
              type="button"
            >
              <Plus className="size-3.5" />
            </button>
          </div>
          <div className="text-muted-foreground flex items-center gap-2 border-b px-3 py-1.5 text-[10px] font-medium tracking-widest uppercase">
            <Database className="size-3.5" />
            SQL editor
          </div>
          <textarea
            aria-label="SQL query"
            className="bg-background text-foreground placeholder:text-muted-foreground min-h-44 w-full resize-y border-b p-3 font-mono text-xs outline-none"
            onChange={(event) => updateActiveTab({ sql: event.target.value })}
            spellCheck={false}
            value={sql}
          />
          <div className="flex flex-wrap items-center gap-2 border-b p-2">
            <Button
              disabled={!sql.trim() || running}
              onClick={() => void runQuery()}
              size="sm"
            >
              <Play className="size-3.5" />
              {running ? 'Running…' : 'Run query'}
            </Button>
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <Input
                aria-label="Saved query name"
                className="max-w-64"
                onChange={(event) => setName(event.target.value)}
                placeholder="Name to save as"
                value={name}
              />
              <Button
                disabled={!name.trim() || !sql.trim()}
                onClick={() => void saveQuery()}
                size="sm"
                variant="outline"
              >
                <Save className="size-3.5" />
                Save
              </Button>
            </div>
            <span className="text-muted-foreground font-mono text-[10px]">
              {message}
            </span>
          </div>
          <QueryResults result={result} />
        </div>
      </div>
    </Page>
  )
}

function QueryResults({ result }: { result: ObservatoryQueryResult | null }) {
  if (!result)
    return (
      <EmptyBlock
        className="py-10"
        description="Run a read-only SELECT statement to inspect provider-backed rows."
        title="No query result yet"
      />
    )
  return (
    <ScrollArea className="max-h-96">
      <table className="w-full text-left">
        <thead className="bg-muted/50 sticky top-0">
          <tr>
            {result.columns.map((column) => (
              <th
                className="text-muted-foreground border-b px-3 py-1.5 font-mono text-[10px] font-medium tracking-widest uppercase"
                key={column}
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {result.rows.map((row, index) => (
            <tr key={index}>
              {result.columns.map((column) => (
                <td
                  className="text-foreground px-3 py-1.5 font-mono text-[11px] whitespace-nowrap"
                  key={column}
                >
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollArea>
  )
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
