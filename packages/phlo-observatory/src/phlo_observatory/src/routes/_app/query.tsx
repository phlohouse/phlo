/** Defines the SQL workspace for running, explaining, and saving queries. */
import * as React from 'react'
import { createFileRoute, getRouteApi } from '@tanstack/react-router'
import { z } from 'zod'
import {
  CheckIcon,
  CircleCheckIcon,
  DownloadIcon,
  GitBranchIcon,
  Loader2Icon,
  PencilIcon,
  PinIcon,
  PlayIcon,
  PlusIcon,
  SquareIcon,
  TableIcon,
  Trash2Icon,
  XIcon,
} from 'lucide-react'
import type { QueryTab, QueryTable } from '@/lib/query-workspace'
import type { QuerySession } from '@/lib/data/api/query'
import type { IncidentRecord } from '@/lib/data/api/incidents'
import {
  cancelQuery,
  deleteSavedQuery,
  downloadQueryCsv,
  explainQuery,
  getQueryIncidentTargets,
  getQuerySession,
  getQueryWorkspace,
  pinQueryToIncident,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/menu'
import { Segmented } from '@/components/ui/toggle-group'
import { cn } from '@/lib/utils'
import { incidentOperationKey } from '@/lib/data/api/incidents'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Select } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import {
  closeQueryTab,
  isCurrentQueryResponse,
  nextQueryTabFocus,
  openQueryTablePreview,
  queryDraftChangedSinceAttempt,
  queryInitialSql,
  queryTableName,
  queryTableSql,
  queryWorkspaceKey,
  renameQueryTab,
  restoreQueryWorkspace,
  storeQueryWorkspace,
} from '@/lib/query-workspace'

const appRoute = getRouteApi('/_app')

export const Route = createFileRoute('/_app/query')({
  validateSearch: z.object({
    sql: z
      .string()
      .max(64 * 1024)
      .optional(),
  }),
  loaderDeps: ({ search }) => ({ env: search.env, sql: search.sql }),
  loader: ({ deps }) => getQueryWorkspace({ data: deps }),
  head: () => ({ meta: [{ title: 'Query · phlo' }] }),
  component: QueryPage,
})

type Tab = QueryTab
const terminal = new Set<QuerySession['status']>([
  'completed',
  'failed',
  'cancelled',
])

function QueryStatusMessage({
  tab,
  isSubmitting,
  running,
  result,
  cancelled,
}: Pick<
  Parameters<typeof QueryStatus>[0],
  'tab' | 'isSubmitting' | 'running' | 'result' | 'cancelled'
>) {
  if (isSubmitting)
    return (
      <>
        <Loader2Icon className="size-3.5 animate-spin" /> Submitting…
      </>
    )
  if (running)
    return (
      <>
        <Loader2Icon className="size-3.5 animate-spin" />
        {tab.session?.status}…
        {tab.error ? (
          <span className="text-bad-text">
            Status check failed: {tab.error}; retrying…
          </span>
        ) : null}
      </>
    )
  if (tab.error) return <span className="text-bad-text">{tab.error}</span>
  if (result)
    return (
      <>
        <CircleCheckIcon className="size-3.5 text-ok-text" />{' '}
        {result.rows.length} {result.rows.length === 1 ? 'row' : 'rows'}
        {result.has_more ? ' (more available)' : ''}
      </>
    )
  return <span>{cancelled ? 'Cancelled' : 'Not run yet'}</span>
}

function QueryStatus({
  tab,
  isSubmitting,
  running,
  result,
  cancelled,
  csv,
  pin,
}: {
  tab: Tab
  isSubmitting: boolean
  running: boolean | undefined
  result: QuerySession['result'] | null
  cancelled: boolean
  csv: () => Promise<void>
  pin: () => void
}) {
  return (
    <div
      role="status"
      className="flex min-h-10 shrink-0 flex-wrap items-center gap-2 border-y border-line bg-raised px-4 py-2 text-[12.5px] text-muted-foreground"
    >
      {queryDraftChangedSinceAttempt(tab) ? (
        <p className="m-0 w-full">
          SQL changed since the last attempt. Run to update results.
        </p>
      ) : null}
      <div className="flex min-w-0 flex-1 basis-full flex-wrap items-center gap-2 sm:basis-auto">
        <QueryStatusMessage
          tab={tab}
          isSubmitting={isSubmitting}
          running={running}
          result={result}
          cancelled={cancelled}
        />
      </div>
      <Button
        variant="outline"
        size="sm"
        className="ml-auto"
        disabled={
          !result || !tab.session?.evidence_available || tab.mode === 'plan'
        }
        title={
          tab.session?.evidence_available
            ? 'Pin the completed execution, not unsaved SQL edits'
            : 'Pinning requires durable query evidence storage'
        }
        onClick={pin}
      >
        <PinIcon />{' '}
        {tab.pinnedIncident
          ? `Pinned to #${tab.pinnedIncident}`
          : 'Pin to incident'}
      </Button>
      <Button
        variant="outline"
        size="sm"
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
              ? queryDraftChangedSinceAttempt(tab)
                ? 'Previous query failed'
                : 'Query failed'
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
  panelId,
}: {
  tabs: Array<Tab>
  activeId: string
  onSelect: (id: string) => void
  onNew: () => void
  panelId: (id: string) => string
}) {
  const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([])
  React.useEffect(() => {
    tabRefs.current[
      tabs.findIndex((item) => item.id === activeId)
    ]?.scrollIntoView({
      block: 'nearest',
      inline: 'nearest',
    })
  }, [activeId, tabs])
  const focusTab = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    const target = nextQueryTabFocus(event.key, tabs.length, index)
    if (target === index || target < 0) return
    event.preventDefault()
    const next = tabs[target]
    if (!next) return
    onSelect(next.id)
    tabRefs.current[target]?.focus()
  }
  return (
    <>
      <div
        role="tablist"
        aria-label="Open queries"
        className="order-last -mx-4 flex w-[calc(100%+2rem)] gap-1 overflow-x-auto px-4 [scrollbar-width:none] lg:order-none lg:mx-0 lg:w-auto lg:min-w-0 lg:flex-1 lg:px-0"
      >
        {tabs.map((item, index) => (
          <button
            key={item.id}
            ref={(element) => {
              tabRefs.current[index] = element
            }}
            id={`query-tab-${item.id}`}
            type="button"
            role="tab"
            aria-controls={panelId(item.id)}
            aria-selected={item.id === activeId}
            tabIndex={item.id === activeId ? 0 : -1}
            onKeyDown={(event) => focusTab(event, index)}
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
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="New query tab"
        onClick={onNew}
        className="order-last lg:order-none"
      >
        <PlusIcon />
      </Button>
    </>
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
  rename: () => void
  close: () => void
  renaming: boolean
  renameValue: string
  setRenameValue: (value: string) => void
  finishRename: (save: boolean) => void
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
    rename,
    close,
    renaming,
    renameValue,
    setRenameValue,
    finishRename,
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
    <header className="flex min-h-[52px] shrink-0 flex-wrap items-center gap-x-2.5 gap-y-2 border-b border-line px-4 py-2.5 lg:pl-5">
      <h1 className="mr-2 text-sm font-medium">Query</h1>
      <QueryTabs
        tabs={tabs}
        activeId={tab.id}
        onSelect={setActiveId}
        onNew={newTab}
        panelId={(id) => `query-panel-${id}`}
      />
      {renaming ? (
        <div className="flex shrink-0 items-center gap-1">
          <Input
            aria-label="Query name"
            value={renameValue}
            maxLength={120}
            className="h-8 w-40"
            onChange={(event) => setRenameValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') finishRename(true)
              if (event.key === 'Escape') finishRename(false)
            }}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Save query name"
            onClick={() => finishRename(true)}
          >
            <CheckIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Cancel rename"
            onClick={() => finishRename(false)}
          >
            <XIcon />
          </Button>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          onClick={rename}
          aria-label="Rename query"
        >
          <PencilIcon /> Rename
        </Button>
      )}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={close}
        aria-label={`Close ${tab.name}`}
      >
        <XIcon />
      </Button>
      <div className="ml-auto mr-4 flex min-w-0 max-w-full shrink-0 flex-wrap items-center justify-end gap-2 lg:mr-0">
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
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label={`Engine: ${engineId ?? 'none'}`}
            className={cn(
              buttonVariants({ variant: 'outline' }),
              'inline-flex',
            )}
          >
            {engineId === 'trino' ? 'Trino' : 'No engine'}
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem disabled>Trino · configured</DropdownMenuItem>
            <DropdownMenuItem disabled>
              DuckDB · no scoped provider
            </DropdownMenuItem>
            <DropdownMenuItem disabled>
              Spark SQL · not configured
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
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

function initialSql(table: QueryTable | null) {
  return table ? queryTableSql(table) : 'SELECT 1'
}

function firstCatalogTable(
  catalogs: Awaited<
    ReturnType<typeof getQueryWorkspace>
  >['catalog']['catalogs'],
) {
  return (
    catalogs.flatMap((catalogItem) =>
      catalogItem.schemas
        .filter(
          (schema) => !['information_schema', 'system'].includes(schema.name),
        )
        .flatMap((schema) =>
          schema.tables.map((table) => ({
            catalog: catalogItem.name,
            schema: schema.name,
            table,
          })),
        ),
    )[0] ?? null
  )
}

function queryTabName(table: QueryTable | null, fallback: string) {
  return table ? `Query · ${queryTableName(table)}` : fallback
}

function initialWorkspace(
  cacheKey: string,
  suppliedSql: string | undefined,
  firstTable: QueryTable | null,
) {
  if (suppliedSql === undefined && typeof window !== 'undefined') {
    const restored = restoreQueryWorkspace(cacheKey)
    if (restored) return restored
  }
  return {
    tabs: [
      {
        id: 'new-initial',
        name: queryTabName(firstTable, 'Query 1'),
        sql: queryInitialSql(suppliedSql, initialSql(firstTable)),
        mode: 'results' as const,
        ...(suppliedSql === undefined && firstTable
          ? { previewTable: firstTable }
          : {}),
      },
    ],
    activeId: '',
    selected: firstTable,
  }
}

function confirmDiscard(tab: Pick<Tab, 'dirty' | 'name'>, action: string) {
  return (
    !tab.dirty ||
    window.confirm(`Discard unsaved changes ${action} “${tab.name}”?`)
  )
}

function closeActiveQuery({
  tab,
  tabs,
  activeId,
  selected,
  setTabs,
  setActiveId,
}: {
  tab: Tab
  tabs: Array<Tab>
  activeId: string
  selected: QueryTable | null
  setTabs: React.Dispatch<React.SetStateAction<Array<Tab>>>
  setActiveId: React.Dispatch<React.SetStateAction<string>>
}) {
  if (!confirmDiscard(tab, 'to')) return
  const closed = closeQueryTab<Tab>(tabs, activeId, tab.id, () => {
    const id = `new-${crypto.randomUUID()}`
    return {
      id,
      name: queryTabName(selected, 'Query 1'),
      sql: initialSql(selected),
      mode: 'results',
      previewTable: selected ?? undefined,
    }
  })
  setTabs(closed.tabs)
  setActiveId(closed.activeId)
}

function usePersistQueryWorkspace(
  cacheKey: string,
  actor: string,
  suppliedSql: string | undefined,
  workspace: {
    tabs: Array<Tab>
    activeId: string
    selected: QueryTable | null
  },
) {
  React.useEffect(() => {
    if (suppliedSql !== undefined || typeof window === 'undefined') return
    storeQueryWorkspace(cacheKey, actor, workspace)
  }, [actor, cacheKey, suppliedSql, workspace])
}

function changeQueryView(
  view: 'results' | 'chart' | 'plan',
  mode: Tab['mode'],
  start: (mode: Tab['mode'], showChart?: boolean) => Promise<void>,
  setChart: (showChart: boolean) => void,
) {
  if (view === 'plan') {
    void start('plan')
    return
  }
  if (mode === 'plan') void start('results', view === 'chart')
  else setChart(view === 'chart')
}

function queryDisplayState(tab: Tab, pending: Array<string>) {
  return {
    running: tab.session && !terminal.has(tab.session.status),
    cancelled: tab.session?.status === 'cancelled',
    isSubmitting: pending.includes(tab.id),
    result: tab.session?.status === 'completed' ? tab.session.result : null,
  }
}

function QueryPage() {
  const { env, sql } = Route.useSearch()
  const { me } = appRoute.useLoaderData()
  const actor = JSON.stringify([me.principal_type, me.subject])
  const cacheKey = queryWorkspaceKey(env, actor)
  return (
    <QueryWorkspace
      key={JSON.stringify([cacheKey, sql])}
      cacheKey={cacheKey}
      actor={actor}
      suppliedSql={sql}
    />
  )
}

function PinQueryDialog({
  env,
  session,
  open,
  onOpenChange,
  onPinned,
}: {
  env: 'prod' | 'staging'
  session?: QuerySession
  open: boolean
  onOpenChange: (open: boolean) => void
  onPinned: (id: string) => void
}) {
  const [targets, setTargets] = React.useState<Array<IncidentRecord>>([])
  const [target, setTarget] = React.useState('')
  const [error, setError] = React.useState<string>()
  const [busy, setBusy] = React.useState(false)
  React.useEffect(() => {
    if (!open) return
    let stopped = false
    setBusy(true)
    setError(undefined)
    setTarget('')
    getQueryIncidentTargets({ data: { env } }).then(
      (page) => {
        if (!stopped) {
          setTargets(page.incidents)
          setBusy(false)
        }
      },
      (caught) => {
        if (!stopped) {
          setError(
            caught instanceof Error
              ? caught.message
              : 'Could not load incidents.',
          )
          setBusy(false)
        }
      },
    )
    return () => {
      stopped = true
    }
  }, [open, env])
  const pin = async () => {
    const incident = targets.find((item) => item.id === target)
    if (!incident || !session || session.env !== env) return
    setBusy(true)
    setError(undefined)
    try {
      const updated = await pinQueryToIncident({
        data: {
          env,
          id: session.id,
          incident_id: incident.id,
          version: incident.version,
          idempotency_key: incidentOperationKey(
            env,
            incident.id,
            'pin',
            `${session.id}:${incident.version}`,
          ),
        },
      })
      setTargets((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      )
      onPinned(updated.id)
      onOpenChange(false)
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'Could not pin query evidence.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Pin query to incident</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="m-0 text-sm text-muted-foreground">
            Pins the completed {session?.engine} execution in {env}. Incident
            readers see execution identity, not confidential SQL or result rows.
          </p>
          <label className="text-sm" htmlFor="pin-incident">
            Incident
          </label>
          <Select
            id="pin-incident"
            value={target}
            onValueChange={setTarget}
            options={[
              { value: '', label: busy ? 'Loading…' : 'Choose an incident' },
              ...targets.map((item) => ({
                value: item.id,
                label: `#${item.id} · ${item.title}`,
              })),
            ]}
          />
          {!busy && !targets.length && !error ? (
            <p className="text-sm">No incidents in this environment.</p>
          ) : null}
          {error ? (
            <p role="alert" className="text-sm text-bad-text">
              {error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={busy || !target} onClick={pin}>
            {busy ? 'Working…' : 'Pin execution'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function QueryWorkspace({
  cacheKey,
  actor,
  suppliedSql,
}: {
  cacheKey: string
  actor: string
  suppliedSql?: string
}) {
  const data = Route.useLoaderData()
  const { env } = Route.useSearch()
  const firstTable = firstCatalogTable(data.catalog.catalogs)
  const [savedQueries, setSavedQueries] = React.useState(data.saved)
  const [initial] = React.useState(() =>
    initialWorkspace(cacheKey, suppliedSql, firstTable),
  )
  const [tabs, setTabs] = React.useState<Array<Tab>>(initial.tabs)
  const [activeId, setActiveId] = React.useState(
    initial.activeId || initial.tabs[0].id,
  )
  const [chart, setChart] = React.useState(false)
  React.useEffect(() => setChart(false), [activeId, env])
  const [selected, setSelected] = React.useState(initial.selected)
  const [renaming, setRenaming] = React.useState(false)
  const [renameValue, setRenameValue] = React.useState('')
  const [treeOpen, setTreeOpen] = React.useState(false)
  const [pinOpen, setPinOpen] = React.useState(false)
  const mounted = React.useRef(true)
  React.useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
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
    setSavedQueries(data.saved)
  }, [data.saved])

  usePersistQueryWorkspace(cacheKey, actor, suppliedSql, {
    tabs,
    activeId,
    selected,
  })

  React.useEffect(() => {
    const session = tab.session
    if (!session || terminal.has(session.status)) return
    let stopped = false
    let timer: number
    const poll = async () => {
      try {
        const next = await getQuerySession({ data: { env, id: session.id } })
        if (!isCurrentQueryResponse(!stopped, env, next.env)) return
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
    patch(tab.id, {
      mode,
      submittedSql: tab.sql,
      session: undefined,
      error: undefined,
      pinnedIncident: undefined,
    })
    try {
      const session = await (mode === 'plan' ? explainQuery : submitQuery)({
        data: { env, sql: tab.sql },
      })
      if (!isCurrentQueryResponse(mounted.current, env, session.env)) return
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
    const id = `new-${crypto.randomUUID()}`
    const name = queryTabName(selected, `Query ${tabs.length + 1}`)
    setTabs((items) => [
      ...items,
      {
        id,
        name,
        sql: initialSql(selected),
        mode: 'results',
        previewTable: selected ?? undefined,
      },
    ])
    setActiveId(id)
  }
  const selectTable = (table: QueryTable) => {
    const next = openQueryTablePreview(
      { tabs, activeId, selected },
      table,
      `new-${crypto.randomUUID()}`,
    )
    setTabs(next.tabs)
    setActiveId(next.activeId)
    setSelected(next.selected)
    setTreeOpen(false)
  }
  const rename = () => {
    setRenameValue(tab.name)
    setRenaming(true)
  }
  const finishRename = (save: boolean) => {
    setRenaming(false)
    patch(tab.id, renameQueryTab(tab, renameValue, save))
  }
  const close = () =>
    closeActiveQuery({ tab, tabs, activeId, selected, setTabs, setActiveId })
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
      patch(tab.id, { id: saved.id, saved, name: saved.name, dirty: false })
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
        previewTable: selected ?? undefined,
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
      if (!isCurrentQueryResponse(mounted.current, env, tab.session.env)) return
      const url = URL.createObjectURL(
        new Blob([text], { type: 'text/csv;charset=utf-8' }),
      )
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `query-${tab.session.id}.csv`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      if (!mounted.current) return
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
      if (!isCurrentQueryResponse(mounted.current, env, session.env)) return
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
  const { running, cancelled, isSubmitting, result } = queryDisplayState(
    tab,
    pending,
  )
  const tree = (
    <CatalogTree
      catalog={data.catalog.catalogs}
      saved={savedQueries}
      selected={selected ? queryTableName(selected) : ''}
      onSelect={selectTable}
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
        rename={rename}
        close={close}
        renaming={renaming}
        renameValue={renameValue}
        setRenameValue={setRenameValue}
        finishRename={finishRename}
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
          id={`query-panel-${tab.id}`}
          role="tabpanel"
          aria-labelledby={`query-tab-${tab.id}`}
          aria-label="Workspace"
          className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto lg:overflow-hidden"
        >
          {tabs
            .filter((item) => item.id !== activeId)
            .map((item) => (
              <div
                key={item.id}
                id={`query-panel-${item.id}`}
                role="tabpanel"
                aria-labelledby={`query-tab-${item.id}`}
                hidden
              />
            ))}
          <SqlEditor
            key={tab.id}
            value={tab.sql}
            onChange={(sql) => patch(tab.id, { sql, dirty: true })}
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
            pin={() => setPinOpen(true)}
          />
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line px-4 py-2">
            <Segmented
              aria-label="Result view"
              value={tab.mode === 'plan' ? 'plan' : chart ? 'chart' : 'results'}
              onValueChange={(view: 'results' | 'chart' | 'plan') =>
                changeQueryView(view, tab.mode, start, setChart)
              }
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
      <PinQueryDialog
        env={env}
        session={tab.session}
        open={pinOpen}
        onOpenChange={setPinOpen}
        onPinned={(id) => patch(tab.id, { pinnedIncident: id })}
      />
    </>
  )
}
