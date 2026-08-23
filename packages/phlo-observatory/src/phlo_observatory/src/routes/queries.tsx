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
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  readQueryWorkspace,
  recordQueryExecution,
  writeQueryWorkspace,
} from '@/observatory/shell/localActivity'

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
    <ObservatoryPage
      kicker="Data"
      title="Queries"
      description="A read-only SQL workspace backed by the active query provider, with project-persisted saved queries."
      action={
        <span className="phlo-observatory-pill">
          {savedQueries.length} saved
        </span>
      }
    >
      <section className="phlo-observatory-query-workspace">
        <aside className="phlo-observatory-query-library">
          <div className="phlo-observatory-workspace-toolbar">
            <span>
              <Save className="size-4" />
              Saved queries
            </span>
            <button aria-label="New query" onClick={newScratch} type="button">
              <Plus className="size-3.5" />
              New
            </button>
          </div>
          <div className="phlo-observatory-detail-list">
            {savedQueries.map((query) => (
              <button
                className="phlo-observatory-mini-row"
                key={query.id}
                onClick={() => openSavedQuery(query)}
                type="button"
              >
                <span>{query.name}</span>
                <small>{query.branch ?? 'main'}</small>
              </button>
            ))}
            {!savedQueries.length && (
              <div className="phlo-observatory-mini-row">
                <span>No saved queries</span>
                <small>Save the editor contents to create one</small>
              </div>
            )}
          </div>
        </aside>
        <div className="phlo-observatory-query-editor-surface">
          <div className="phlo-observatory-query-tabs" role="tablist">
            {workspace.tabs.map((tab) => (
              <button
                aria-selected={tab.id === workspace.activeId}
                className="phlo-observatory-query-tab"
                data-active={tab.id === workspace.activeId}
                key={tab.id}
                onClick={() =>
                  setWorkspace((current) => ({ ...current, activeId: tab.id }))
                }
                role="tab"
                type="button"
              >
                <span>{tab.name}</span>
                {workspace.tabs.length > 1 && (
                  <X
                    aria-label={`Close ${tab.name}`}
                    className="size-3"
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
              className="phlo-observatory-query-tab-add"
              onClick={newScratch}
              type="button"
            >
              <Plus className="size-3.5" />
            </button>
          </div>
          <div className="phlo-observatory-workspace-toolbar">
            <span>
              <Database className="size-4" />
              SQL editor
            </span>
            <span className="phlo-observatory-pill">Read only</span>
            <Link
              className="phlo-observatory-query-history-link"
              to="/query-history"
            >
              <History className="size-3.5" />
              History
            </Link>
          </div>
          <textarea
            aria-label="SQL query"
            onChange={(event) => updateActiveTab({ sql: event.target.value })}
            spellCheck={false}
            value={sql}
          />
          <div className="phlo-observatory-query-actions">
            <button
              disabled={!sql.trim() || running}
              onClick={() => void runQuery()}
              type="button"
            >
              <Play className="size-3.5" />
              {running ? 'Running…' : 'Run query'}
            </button>
            <label>
              <span>Saved query name</span>
              <input
                onChange={(event) => setName(event.target.value)}
                placeholder="Daily revenue sample"
                value={name}
              />
            </label>
            <button
              disabled={!name.trim() || !sql.trim()}
              onClick={() => void saveQuery()}
              type="button"
            >
              <Save className="size-3.5" />
              Save
            </button>
          </div>
          <div className="phlo-observatory-panel-note">{message}</div>
          <QueryResults result={result} />
        </div>
      </section>
    </ObservatoryPage>
  )
}

function QueryResults({ result }: { result: ObservatoryQueryResult | null }) {
  if (!result)
    return (
      <div className="phlo-observatory-operation-empty">
        <div>
          <h2>No query result yet</h2>
          <p>
            Run a read-only SELECT statement to inspect provider-backed rows.
          </p>
        </div>
      </div>
    )
  return (
    <div className="phlo-observatory-query-results">
      <table>
        <thead>
          <tr>
            {result.columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, index) => (
            <tr key={index}>
              {result.columns.map((column) => (
                <td key={column}>{formatCell(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
