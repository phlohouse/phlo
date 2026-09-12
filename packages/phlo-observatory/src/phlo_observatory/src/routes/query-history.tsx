/**
 * /query-history route. Shows SQL executions recorded in this browser's
 * local activity log, kept separate from orchestrator pipeline runs.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Play } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { ObservatoryQueryExecution } from '@/observatory/shell/localActivity'
import {
  localActivityEvent,
  readQueryHistory,
} from '@/observatory/shell/localActivity'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { EmptyBlock } from '@/components/observatory/states'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { StatusBadge } from '@/components/observatory/status'
import { formatDateTime } from '@/components/observatory/time'
import { SectionCard } from '@/components/observatory/section'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

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
    <Page>
      <PageHeader
        actions={<Badge variant="secondary">{history.length} executions</Badge>}
        description="Read-only SQL executions started from this browser — separate from orchestrator and pipeline runs."
        title="Query history"
      />
      <StatGrid className="xl:grid-cols-3">
        <StatCard label="Succeeded" state="ok" value={counts.succeeded} />
        <StatCard
          label="Failed"
          state={counts.failed > 0 ? 'error' : 'ok'}
          value={counts.failed}
        />
        <StatCard label="Recorded" value={history.length} />
      </StatGrid>
      <SplitView
        inspector={
          selected ? (
            <>
              <InspectorSection label="Selected execution">
                <StatusBadge
                  label={selected.status}
                  state={selected.status === 'succeeded' ? 'ok' : 'error'}
                />
                <pre className="bg-muted text-foreground mt-2 overflow-x-auto rounded-md p-2 font-mono text-[11px] whitespace-pre-wrap">
                  {selected.sql}
                </pre>
              </InspectorSection>
              <InspectorSection label="Details">
                <FactGrid>
                  <Fact
                    label="Started"
                    value={formatDateTime(selected.startedAt)}
                  />
                  <Fact label="Duration" value={`${selected.durationMs}ms`} />
                  <Fact label="Rows" value={selected.rowCount} />
                  <Fact label="Scope" value="This browser" />
                </FactGrid>
              </InspectorSection>
              {selected.error && (
                <InspectorSection label="Error">
                  <p className="text-status-error font-mono text-[11px] break-all">
                    {selected.error}
                  </p>
                </InspectorSection>
              )}
            </>
          ) : (
            <InspectorSection label="Selected execution">
              <p className="text-muted-foreground text-xs">
                Query history is intentionally separate from pipeline runs and
                stays browser-local.
              </p>
            </InspectorSection>
          )
        }
        list={
          <SectionCard className="ring-0" title="Executions">
            {history.length ? (
              <div className="divide-y divide-border">
                {history.map((item) => (
                  <button
                    className={cn(
                      'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-3 px-3 py-2 text-left transition-colors',
                      item.id === selected?.id && 'bg-accent/60',
                    )}
                    key={item.id}
                    onClick={() => setSelectedId(item.id)}
                    type="button"
                  >
                    <span className="min-w-0">
                      <span className="text-foreground block truncate font-mono text-[11px]">
                        {compactSql(item.sql)}
                      </span>
                      <span className="text-muted-foreground block font-mono text-[10px]">
                        {formatDateTime(item.startedAt)}
                      </span>
                    </span>
                    <span className="text-muted-foreground font-mono text-[10px]">
                      {item.durationMs}ms
                    </span>
                    <span className="text-muted-foreground font-mono text-[10px]">
                      {item.rowCount} rows
                    </span>
                    <Badge
                      className="font-mono text-[10px]"
                      variant="secondary"
                    >
                      {item.status}
                    </Badge>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyBlock
                action={
                  <Link
                    className={cn(buttonVariants({ size: 'sm' }))}
                    to="/queries"
                  >
                    <Play className="size-3.5" />
                    Open query workbench
                  </Link>
                }
                description="Run a query from the SQL workbench to create browser-local execution evidence."
                title="No query executions yet"
              />
            )}
          </SectionCard>
        }
      />
    </Page>
  )
}

function compactSql(sql: string): string {
  const compact = sql.replace(/\s+/g, ' ').trim()
  return compact.length > 90 ? `${compact.slice(0, 87)}…` : compact
}
