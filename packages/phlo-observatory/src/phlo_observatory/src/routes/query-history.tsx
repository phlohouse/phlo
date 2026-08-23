/**
 * /query-history route. Shows SQL executions recorded in this browser's
 * local activity log, kept separate from orchestrator pipeline runs.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Clock3, Database, Play } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { ObservatoryQueryExecution } from '@/observatory/shell/localActivity'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  localActivityEvent,
  readQueryHistory,
} from '@/observatory/shell/localActivity'

export const Route = createFileRoute('/query-history')({
  component: QueryHistory,
})

export function QueryHistory() {
  const [history, setHistory] = useState<Array<ObservatoryQueryExecution>>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  useEffect(() => {
    const refresh = () => setHistory(readQueryHistory())
    refresh()
    window.addEventListener(localActivityEvent, refresh)
    return () => window.removeEventListener(localActivityEvent, refresh)
  }, [])
  const selected =
    history.find((item) => item.id === selectedId) ?? history[0] ?? null
  const counts = useMemo(
    () => ({
      succeeded: history.filter((item) => item.status === 'succeeded').length,
      failed: history.filter((item) => item.status === 'failed').length,
    }),
    [history],
  )

  return (
    <ObservatoryPage
      kicker="Data"
      title="Query history"
      description="Read-only SQL executions started from this browser, separated from orchestrator and pipeline runs."
      action={
        <span className="phlo-observatory-pill">
          {history.length} executions
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-local-index-shell">
        <div className="phlo-observatory-command-primary">
          <div className="phlo-observatory-command-strip phlo-observatory-query-history-summary">
            <HistoryMetric label="Succeeded" value={counts.succeeded} />
            <HistoryMetric label="Failed" value={counts.failed} />
            <HistoryMetric label="Recorded" value={history.length} />
          </div>
          <div className="phlo-observatory-workspace-toolbar">
            <span>
              <Clock3 className="size-4" />
              Executions
            </span>
            <span className="phlo-observatory-pill">Browser-local</span>
          </div>
          {history.length ? (
            history.map((item) => (
              <button
                className="phlo-observatory-query-history-row"
                data-selected={item.id === selected?.id}
                key={item.id}
                onClick={() => setSelectedId(item.id)}
                type="button"
              >
                <span
                  className="phlo-observatory-dot"
                  data-state={item.status === 'succeeded' ? 'ok' : 'error'}
                />
                <span>
                  <strong>{compactSql(item.sql)}</strong>
                  <small>{new Date(item.startedAt).toLocaleString()}</small>
                </span>
                <span>{item.status}</span>
                <span>{item.durationMs}ms</span>
                <span>{item.rowCount} rows</span>
              </button>
            ))
          ) : (
            <div className="phlo-observatory-operation-empty">
              <div>
                <h2>No query executions yet</h2>
                <p>
                  Run a query from the SQL workspace to create browser-local
                  execution evidence.
                </p>
                <Link className="phlo-observatory-map-action" to="/queries">
                  <Play className="size-3.5" />
                  Open Queries
                </Link>
              </div>
            </div>
          )}
        </div>
        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">
            Selected execution
          </div>
          {selected ? (
            <>
              <h2>{selected.status}</h2>
              <p>{selected.sql}</p>
              <dl className="phlo-observatory-facts">
                <dt>Started</dt>
                <dd>{new Date(selected.startedAt).toLocaleString()}</dd>
                <dt>Duration</dt>
                <dd>{selected.durationMs}ms</dd>
                <dt>Rows</dt>
                <dd>{selected.rowCount}</dd>
                <dt>Scope</dt>
                <dd>Current browser</dd>
              </dl>
              {selected.error && (
                <div className="phlo-observatory-panel-note">
                  {selected.error}
                </div>
              )}
            </>
          ) : (
            <>
              <h2>No execution selected</h2>
              <p>
                Query history is intentionally separate from pipeline Runs and
                clearly labelled as browser-local evidence.
              </p>
            </>
          )}
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function HistoryMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="phlo-observatory-command-metric">
      <Database className="size-4" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function compactSql(sql: string): string {
  const compact = sql.replace(/\s+/g, ' ').trim()
  return compact.length > 90 ? `${compact.slice(0, 87)}…` : compact
}
