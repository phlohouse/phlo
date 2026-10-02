/** Defines the SQL workspace for running, explaining, and saving queries. */
import * as React from 'react'
import { createFileRoute } from '@tanstack/react-router'
import {
  CircleCheckIcon,
  DownloadIcon,
  GitBranchIcon,
  Loader2Icon,
  PlayIcon,
  PlusIcon,
  SquareIcon,
  TableIcon,
  Trash2Icon,
} from 'lucide-react'
import type { QuerySession, SavedQuery } from '@/lib/data/api/query'
import {
  cancelQuery,
  deleteSavedQuery,
  downloadQueryCsv,
  explainQuery,
  getQuerySession,
  getQueryWorkspace,
  saveQuery,
  submitQuery,
} from '@/lib/data/api/query'
import { CatalogTree } from '@/components/query/catalog-tree'
import { SqlEditor } from '@/components/query/sql-editor'
import {
  PlanView,
  ResultsChart,
  ResultsGrid,
} from '@/components/query/result-views'
import { EmptyState } from '@/components/phlo/states'
import { Mono } from '@/components/phlo/status'
import { Button, buttonVariants } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/menu'
import { Segmented } from '@/components/ui/toggle-group'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/query')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getQueryWorkspace({ data: deps }),
  head: () => ({ meta: [{ title: 'Query · phlo' }] }),
  component: QueryPage,
})

type Tab = {
  id: string
  name: string
  sql: string
  saved?: SavedQuery
  session?: QuerySession
  mode: 'results' | 'plan'
  error?: string
}
const terminal = new Set<QuerySession['status']>([
  'completed',
  'failed',
  'cancelled',
])

function QueryStatus({
  tab,
  isSubmitting,
  running,
  result,
  cancelled,
  csv,
}: {
  tab: Tab
  isSubmitting: boolean
  running: boolean | undefined
  result: QuerySession['result'] | null
  cancelled: boolean
  csv: () => Promise<void>
}) {
  return (
    <div
      role="status"
      className="flex min-h-10 items-center gap-2 border-y border-line bg-raised px-4 text-[12.5px] text-muted-foreground"
    >
      {isSubmitting ? (
        <>
          <Loader2Icon className="size-3.5 animate-spin" /> Submitting…
        </>
      ) : running ? (
        <>
          <Loader2Icon className="size-3.5 animate-spin" />{' '}
          {tab.session?.status}…
          {tab.error ? (
            <span className="text-bad-text">
              Status check failed: {tab.error}; retrying…
            </span>
          ) : null}
        </>
      ) : tab.error ? (
        <span className="text-bad-text">{tab.error}</span>
      ) : result ? (
        <>
          <CircleCheckIcon className="size-3.5 text-ok-text" />{' '}
          {result.rows.length} rows{result.has_more ? ' (more available)' : ''}
        </>
      ) : (
        <span>{cancelled ? 'Cancelled' : 'Not run yet'}</span>
      )}
      <Button
        variant="outline"
        size="sm"
        className="ml-auto"
        disabled={!result || tab.mode === 'plan'}
        onClick={csv}
      >
        <DownloadIcon /> CSV
      </Button>
    </div>
  )
}

function QueryOutput({
  tab,
  chart,
  env,
  refName,
  isSubmitting,
  running,
  result,
  cancelled,
}: {
  tab: Tab
  chart: boolean
  env: string
  refName?: string
  isSubmitting: boolean
  running: boolean | undefined
  result: QuerySession['result'] | null
  cancelled: boolean
}) {
  if (isSubmitting || running)
    return (
      <div className="flex min-h-[420px] flex-1 flex-col lg:min-h-0">
        <div className="flex flex-1 items-center justify-center">
          <Loader2Icon className="size-4 animate-spin" />
        </div>
      </div>
    )
  if (result && !tab.error)
    return (
      <div className="flex min-h-[420px] flex-1 flex-col lg:min-h-0">
        {tab.mode === 'plan' ? (
          <PlanView result={result} />
        ) : chart ? (
          <ResultsChart result={result} />
        ) : result.rows.length ? (
          <ResultsGrid result={result} />
        ) : (
          <div className="p-4">
            <EmptyState title="No rows returned">
              The query completed successfully.
            </EmptyState>
          </div>
        )}
      </div>
    )
  return (
    <div className="flex min-h-[420px] flex-1 flex-col lg:min-h-0">
      <div className="p-4">
        <EmptyState
          title={
            tab.error
              ? 'Query failed'
              : cancelled
                ? 'Query cancelled'
                : 'Not run yet'
          }
          icon={<PlayIcon className="size-3" />}
        >
          {tab.error ??
            (cancelled ? (
              'The query was cancelled. No results are displayed.'
            ) : (
              <>
                Run or explain this query on <Mono>{refName ?? env}</Mono>.
              </>
            ))}
        </EmptyState>
      </div>
    </div>
  )
}

function QueryTabs({
  tabs,
  activeId,
  onSelect,
  onNew,
}: {
  tabs: Array<Tab>
  activeId: string
  onSelect: (id: string) => void
  onNew: () => void
}) {
  return (
    <div
      role="tablist"
      aria-label="Open queries"
      className="order-last -mx-4 flex w-[calc(100%+2rem)] gap-1 overflow-x-auto px-4 [scrollbar-width:none] lg:order-none lg:mx-0 lg:w-auto lg:min-w-0 lg:px-0"
    >
      {tabs.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          aria-selected={item.id === activeId}
          onClick={() => onSelect(item.id)}
          className={cn(
            'flex h-10 shrink-0 cursor-pointer items-center rounded-md border px-2.5 text-[13px] whitespace-nowrap lg:h-[30px]',
            item.id === activeId
              ? 'border-border bg-soft text-foreground'
              : 'border-transparent text-text-3 hover:bg-soft',
          )}
        >
          {item.name}
        </button>
      ))}
      <button
        type="button"
        aria-label="New query tab"
        onClick={onNew}
        className="flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-md text-text-3 hover:bg-soft lg:size-[30px]"
      >
        <PlusIcon className="mx-auto size-3.5" />
      </button>
    </div>
  )
}

type QueryToolbarProps = {
  tab: Tab
  tabs: Array<Tab>
  tree: React.ReactNode
  treeOpen: boolean
  setTreeOpen: (open: boolean) => void
  setActiveId: (id: string) => void
  newTab: () => void
  refName?: string
  engineId?: string
  env: string
  isSubmitting: boolean
  running: boolean | undefined
  remove: () => Promise<void>
  persist: () => Promise<void>
  start: (mode: Tab['mode']) => Promise<void>
  cancel: () => Promise<void>
}

function QueryToolbar(props: QueryToolbarProps) {
  const {
    tab,
    tabs,
    tree,
    treeOpen,
    setTreeOpen,
    setActiveId,
    newTab,
    refName,
    engineId,
    env,
    isSubmitting,
    running,
    remove,
    persist,
    start,
    cancel,
  } = props
  return (
    <header className="flex min-h-[52px] shrink-0 flex-wrap items-center gap-x-2.5 gap-y-2 border-b border-line px-4 py-2.5 lg:flex-nowrap lg:py-0 lg:pl-5">
      <h1 className="mr-2 text-sm font-medium">Query</h1>
      <QueryTabs
        tabs={tabs}
        activeId={tab.id}
        onSelect={setActiveId}
        onNew={newTab}
      />
      <div className="ml-auto flex items-center gap-2">
        <Popover open={treeOpen} onOpenChange={setTreeOpen}>
          <PopoverTrigger
            className={cn(buttonVariants({ variant: 'outline' }), 'lg:hidden')}
          >
            <TableIcon /> Tables
          </PopoverTrigger>
          <PopoverContent className="max-h-[70dvh] w-[320px] overflow-y-auto p-2.5">
            {tree}
          </PopoverContent>
        </Popover>
        <span
          title="The API selects the environment's configured Nessie ref"
          className={cn(
            buttonVariants({ variant: 'outline' }),
            'hidden md:inline-flex',
          )}
        >
          <GitBranchIcon className="text-branch" />
          <Mono>{refName ?? env}</Mono>
        </span>
        <span
          title="Engine selection is fixed by the environment"
          className={cn(
            buttonVariants({ variant: 'outline' }),
            'hidden md:inline-flex',
          )}
        >
          {engineId ?? 'No engine'}
        </span>
        {tab.saved ? (
          <Button
            variant="outline"
            onClick={remove}
            disabled={isSubmitting}
            aria-label="Delete saved query"
          >
            <Trash2Icon />
          </Button>
        ) : null}
        <Button variant="outline" onClick={persist} disabled={isSubmitting}>
          Save
        </Button>
        <Button
          variant="outline"
          onClick={() => start('plan')}
          disabled={Boolean(running) || isSubmitting}
        >
          Explain
        </Button>
        {running ? (
          <Button
            variant="outline"
            onClick={cancel}
            disabled={tab.session?.status === 'cancelling'}
          >
            <SquareIcon /> Cancel
          </Button>
        ) : (
          <Button onClick={() => start('results')} disabled={isSubmitting}>
            <PlayIcon className="fill-current" />{' '}
            {isSubmitting ? 'Submitting…' : 'Run'}
          </Button>
        )}
      </div>
    </header>
  )
}

function initialSql(table: string) {
  return table ? `SELECT *\nFROM ${table}\nLIMIT 100` : 'SELECT 1'
}

function firstCatalogTable(
  catalog: Awaited<ReturnType<typeof getQueryWorkspace>>['catalog']['catalogs'],
) {
  return (
    catalog.flatMap((c) =>
      c.schemas
        .filter((s) => !['information_schema', 'system'].includes(s.name))
        .flatMap((s) => s.tables.map((t) => `${s.name}.${t}`)),
    )[0] ?? ''
  )
}

function QueryPage() {
  const data = Route.useLoaderData()
  const { env } = Route.useSearch()
  const firstTable = firstCatalogTable(data.catalog.catalogs)
  const [savedQueries, setSavedQueries] = React.useState(data.saved)
  const [tabs, setTabs] = React.useState<Array<Tab>>(() => [
    {
      id: 'new-1',
      name: 'Untitled 1',
      sql: initialSql(firstTable),
      mode: 'results',
    },
  ])
  const [activeId, setActiveId] = React.useState('new-1')
  const [chart, setChart] = React.useState(false)
  React.useEffect(() => setChart(false), [activeId, env])
  const [selected, setSelected] = React.useState(firstTable)
  const [treeOpen, setTreeOpen] = React.useState(false)
  const submitting = React.useRef(new Set<string>())
  const [pending, setPending] = React.useState<Array<string>>([])
  const tab = tabs.find((item) => item.id === activeId) ?? tabs[0]
  const patch = React.useCallback(
    (id: string, value: Partial<Tab>) =>
      setTabs((items) =>
        items.map((item) => (item.id === id ? { ...item, ...value } : item)),
      ),
    [],
  )

  React.useEffect(() => {
    const initialId = `new-${crypto.randomUUID()}`
    setSavedQueries(data.saved)
    setTabs([
      {
        id: initialId,
        name: 'Untitled 1',
        sql: initialSql(firstTable),
        mode: 'results',
      },
    ])
    setActiveId(initialId)
    setSelected(firstTable)
    submitting.current.clear()
    setPending([])
  }, [env])

  React.useEffect(() => {
    const session = tab.session
    if (!session || terminal.has(session.status)) return
    let stopped = false
    let timer: number
    const poll = async () => {
      try {
        const next = await getQuerySession({ data: { env, id: session.id } })
        if (stopped) return
        patch(tab.id, { session: next, error: next.error ?? undefined })
        if (!terminal.has(next.status)) timer = window.setTimeout(poll, 750)
      } catch (error) {
        if (stopped) return
        patch(tab.id, {
          error:
            error instanceof Error
              ? error.message
              : 'Could not read query status.',
        })
        timer = window.setTimeout(poll, 1500)
      }
    }
    timer = window.setTimeout(poll, 750)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [env, patch, tab.id, tab.session?.id])

  const start = async (mode: Tab['mode'], showChart = false) => {
    if (
      submitting.current.has(tab.id) ||
      (tab.session && !terminal.has(tab.session.status))
    )
      return
    setChart(showChart)
    submitting.current.add(tab.id)
    setPending((items) => [...items, tab.id])
    patch(tab.id, { mode, session: undefined, error: undefined })
    try {
      const session = await (mode === 'plan' ? explainQuery : submitQuery)({
        data: { env, sql: tab.sql },
      })
      patch(tab.id, { session })
    } catch (error) {
      patch(tab.id, {
        error:
          error instanceof Error
            ? error.message
            : 'Query could not be submitted.',
      })
    } finally {
      submitting.current.delete(tab.id)
      setPending((items) => items.filter((id) => id !== tab.id))
    }
  }
  const openSaved = (id: string) => {
    const saved = savedQueries.find((item) => item.id === id)
    if (!saved) return
    if (!tabs.some((item) => item.id === id))
      setTabs((items) => [
        ...items,
        { id, name: saved.name, sql: saved.sql, saved, mode: 'results' },
      ])
    setActiveId(id)
    setTreeOpen(false)
  }
  const newTab = () => {
    const n = tabs.length + 1
    const id = `new-${crypto.randomUUID()}`
    setTabs((items) => [
      ...items,
      {
        id,
        name: `Untitled ${n}`,
        sql: initialSql(selected),
        mode: 'results',
      },
    ])
    setActiveId(id)
  }
  const persist = async () => {
    if (submitting.current.has(tab.id)) return
    submitting.current.add(tab.id)
    setPending((items) => [...items, tab.id])
    try {
      const saved = await saveQuery({
        data: {
          env,
          id: tab.saved?.id,
          version: tab.saved?.version,
          name: tab.name,
          sql: tab.sql,
          idempotencyKey: crypto.randomUUID(),
        },
      })
      setSavedQueries((items) => [
        ...items.filter((item) => item.id !== saved.id),
        saved,
      ])
      patch(tab.id, { id: saved.id, saved, name: saved.name })
      setActiveId(saved.id)
    } catch (error) {
      patch(tab.id, {
        error:
          error instanceof Error ? error.message : 'Query could not be saved.',
      })
    } finally {
      submitting.current.delete(tab.id)
      setPending((items) => items.filter((id) => id !== tab.id))
    }
  }
  const remove = async () => {
    if (!tab.saved || submitting.current.has(tab.id)) return
    submitting.current.add(tab.id)
    setPending((items) => [...items, tab.id])
    try {
      await deleteSavedQuery({
        data: {
          env,
          id: tab.saved.id,
          version: tab.saved.version,
          idempotencyKey: crypto.randomUUID(),
        },
      })
      const remaining = tabs.filter((item) => item.id !== tab.id)
      const next = remaining[0] ?? {
        id: `new-${crypto.randomUUID()}`,
        name: 'Untitled 1',
        sql: initialSql(selected),
        mode: 'results' as const,
      }
      setSavedQueries((items) =>
        items.filter((item) => item.id !== tab.saved?.id),
      )
      setTabs(remaining.length ? remaining : [next])
      setActiveId(next.id)
    } catch (error) {
      patch(tab.id, {
        error:
          error instanceof Error
            ? error.message
            : 'Query could not be deleted.',
      })
    } finally {
      submitting.current.delete(tab.id)
      setPending((items) => items.filter((id) => id !== tab.id))
    }
  }
  const csv = async () => {
    if (!tab.session) return
    try {
      const text = await downloadQueryCsv({ data: { env, id: tab.session.id } })
      const url = URL.createObjectURL(
        new Blob([text], { type: 'text/csv;charset=utf-8' }),
      )
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `query-${tab.session.id}.csv`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      patch(tab.id, {
        error:
          error instanceof Error
            ? error.message
            : 'CSV could not be downloaded.',
      })
    }
  }
  const cancel = async () => {
    if (!tab.session) return
    const previous = tab.session
    try {
      patch(tab.id, {
        error: undefined,
        session: { ...previous, status: 'cancelling' },
      })
      const session = await cancelQuery({ data: { env, id: previous.id } })
      patch(tab.id, { session })
    } catch (error) {
      patch(tab.id, {
        session: previous,
        error:
          error instanceof Error
            ? error.message
            : 'Query could not be cancelled.',
      })
    }
  }
  const running = tab.session && !terminal.has(tab.session.status)
  const cancelled = tab.session?.status === 'cancelled'
  const isSubmitting = pending.includes(tab.id)
  const result = tab.session?.status === 'completed' ? tab.session.result : null
  const tree = (
    <CatalogTree
      catalog={data.catalog.catalogs}
      saved={savedQueries}
      selected={selected}
      onSelect={setSelected}
      onOpenSaved={openSaved}
      activeSaved={tab.saved?.id}
    />
  )

  return (
    <>
      <QueryToolbar
        tab={tab}
        tabs={tabs}
        tree={tree}
        treeOpen={treeOpen}
        setTreeOpen={setTreeOpen}
        setActiveId={setActiveId}
        newTab={newTab}
        refName={data.refs[0]?.name}
        engineId={data.engines[0]?.id}
        env={env}
        isSubmitting={isSubmitting}
        running={running}
        remove={remove}
        persist={persist}
        start={start}
        cancel={cancel}
      />
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label="Catalog"
          className="hidden w-[260px] shrink-0 overflow-y-auto border-r border-line p-2.5 lg:block"
        >
          {tree}
        </aside>
        <section
          aria-label="Workspace"
          className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto lg:overflow-hidden"
        >
          <SqlEditor
            key={tab.id}
            value={tab.sql}
            onChange={(sql) => patch(tab.id, { sql })}
            onRun={() => start('results')}
            catalog={data.catalog.catalogs}
            label={`SQL for ${tab.name}`}
            className="h-[264px] shrink-0 lg:h-[328px]"
          />
          <QueryStatus
            tab={tab}
            isSubmitting={isSubmitting}
            running={running}
            result={result}
            cancelled={cancelled}
            csv={csv}
          />
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line px-4 py-2">
            <Segmented
              aria-label="Result view"
              value={tab.mode === 'plan' ? 'plan' : chart ? 'chart' : 'results'}
              onValueChange={(view: 'results' | 'chart' | 'plan') => {
                if (view === 'plan') {
                  void start('plan')
                  return
                }
                if (tab.mode === 'plan') void start('results', view === 'chart')
                else setChart(view === 'chart')
              }}
              options={[
                {
                  value: 'results',
                  label: `Results${result && tab.mode === 'results' ? ` · ${result.rows.length}` : ''}`,
                },
                { value: 'chart', label: 'Chart' },
                { value: 'plan', label: 'Plan' },
              ]}
            />
          </div>
          <QueryOutput
            tab={tab}
            chart={chart}
            env={env}
            refName={data.refs[0]?.name}
            isSubmitting={isSubmitting}
            running={running}
            result={result}
            cancelled={cancelled}
          />
        </section>
      </div>
    </>
  )
}
