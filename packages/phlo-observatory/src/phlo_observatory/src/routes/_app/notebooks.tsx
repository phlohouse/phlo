/** Notebook workspace: .ipynb files in the project, run on a Jupyter kernel, drawn by Observatory. */
import * as React from 'react'
import { createFileRoute, useBlocker, useRouter } from '@tanstack/react-router'
import { z } from 'zod'
import {
  ChevronDownIcon,
  ChevronUpIcon,
  FilesIcon,
  GitBranchIcon,
  LockIcon,
  PlayIcon,
  PlusIcon,
  SquareIcon,
} from 'lucide-react'
import type {
  CellRun,
  KernelVariable,
  KernelView,
  NotebookDocument,
} from '@/lib/data/api/notebooks'
import type { CellState, NotebookCell } from '@/lib/notebook'
import type { KernelPhase } from '@/components/notebooks/kernel-menu'
import {
  bindQueryResult,
  controlKernel,
  getKernelVariables,
  getNotebookWorkspace,
  notebookPathSchema,
  runDuckdb,
  runPython,
  saveNotebook,
  startKernel,
} from '@/lib/data/api/notebooks'
import { getQuerySession, submitQuery } from '@/lib/data/api/query'
import {
  cellLabel,
  freeName,
  newCell,
  newCellId,
  outline,
  sameCells,
  sqlReads,
  staleCells,
  starterCells,
} from '@/lib/notebook'
import { NotebookCellView } from '@/components/notebooks/cell'
import { NotebookExplorer } from '@/components/notebooks/explorer'
import { CatalogBrowser } from '@/components/notebooks/catalog-browser'
import { KernelMenu } from '@/components/notebooks/kernel-menu'
import { NewNotebookDialog } from '@/components/notebooks/new-notebook'
import { EmptyState, PageSkeleton, RouteError } from '@/components/phlo/states'
import { Dot, Mono } from '@/components/phlo/status'
import { Button, buttonVariants } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/menu'
import { Segmented } from '@/components/ui/toggle-group'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/notebooks')({
  validateSearch: z.object({
    path: notebookPathSchema.optional().catch(undefined),
  }),
  loaderDeps: ({ search }) => ({ env: search.env, path: search.path }),
  loader: ({ deps }) => getNotebookWorkspace({ data: deps }),
  head: () => ({ meta: [{ title: 'Notebooks · phlo' }] }),
  pendingComponent: PageSkeleton,
  errorComponent: RouteError,
  component: NotebooksPage,
})

type Workspace = Awaited<ReturnType<typeof getNotebookWorkspace>>
type KernelCall<T> =
  | { state: 'ok'; data: T }
  | { state: 'gone' }
  | { state: 'busy' }
  | { state: 'disabled'; message: string }
type HistoryEntry = {
  key: number
  cell: string
  label: string
  status: CellState['status']
  ms?: number
  at: number
}

const POLL_MS = 400
const QUERY_DEADLINE_MS = 5 * 60_000

function fileName(path: string) {
  return path.split('/').at(-1) ?? path
}

function NotebooksPage() {
  const data = Route.useLoaderData()
  const { env } = Route.useSearch()
  const router = useRouter()
  const navigate = Route.useNavigate()
  const [creating, setCreating] = React.useState(false)
  const paths = data.list.items.map((item) => item.path)
  const folders = [
    ...new Set(
      paths.flatMap((item) => {
        const parts = item.split('/').slice(0, -1)
        return parts.map((_, index) => parts.slice(0, index + 1).join('/'))
      }),
    ),
  ].sort()
  const dialog = (
    <NewNotebookDialog
      open={creating}
      onOpenChange={setCreating}
      folders={folders}
      onCreate={(path) => create(path)}
    />
  )
  const create = async (path: string) => {
    try {
      const saved = await saveNotebook({
        data: { env, path, cells: starterCells(), expected_digest: null },
      })
      if (saved.state === 'conflict') return 'A notebook with that name exists.'
      await router.invalidate()
      // Not awaited: the unsaved-changes prompt can cancel the move, and the new file stays listed.
      void navigate({ search: { env, path } })
      return null
    } catch (error) {
      return error instanceof Error ? error.message : 'Could not create it.'
    }
  }
  if (data.notebook.state === 'open')
    return (
      <>
        <NotebookWorkspace
          key={`${env}:${data.notebook.document.path}`}
          workspace={data}
          document={data.notebook.document}
          onNew={() => setCreating(true)}
        />
        {dialog}
      </>
    )
  const explorer = (
    <NotebookExplorer
      env={env}
      paths={data.list.items.map((item) => item.path)}
      active={
        data.notebook.state === 'unreadable' ? data.notebook.path : undefined
      }
      dirty={false}
      truncated={data.list.truncated}
      reads={[]}
      onNew={() => setCreating(true)}
    />
  )
  return (
    <>
      <header className="flex min-h-[52px] shrink-0 items-center border-b border-line px-4 py-2.5 lg:pl-5">
        <h1 className="text-sm font-medium">Notebooks</h1>
        <Button size="sm" className="ml-auto" onClick={() => setCreating(true)}>
          <PlusIcon /> Notebook
        </Button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside
          aria-label="Notebook files"
          className="shrink-0 border-b border-line p-2.5 lg:w-[240px] lg:overflow-y-auto lg:border-r lg:border-b-0"
        >
          {explorer}
        </aside>
        <div className="flex min-w-0 flex-1 items-start justify-center overflow-y-auto p-6">
          {data.notebook.state === 'unreadable' ? (
            <EmptyState title={`Cannot open ${fileName(data.notebook.path)}`}>
              {data.notebook.message}
            </EmptyState>
          ) : (
            <EmptyState
              title="No notebooks yet"
              className="max-w-md"
              icon={<FilesIcon className="size-3" />}
              action={
                <Button size="sm" onClick={() => setCreating(true)}>
                  <PlusIcon /> New notebook
                </Button>
              }
            >
              Notebooks are .ipynb files in the project&apos;s{' '}
              <code className="font-mono">{data.list.root}</code> folder. Start
              one here, or add one in git.
            </EmptyState>
          )}
        </div>
      </div>
      {dialog}
    </>
  )
}

function NotebookWorkspace({
  workspace,
  document,
  onNew,
}: {
  workspace: Workspace
  document: NotebookDocument
  onNew: () => void
}) {
  const { env } = Route.useSearch()
  const router = useRouter()
  const path = document.path
  const [cells, setCells] = React.useState<Array<NotebookCell>>(document.cells)
  const [saved, setSaved] = React.useState({
    cells: document.cells,
    digest: document.digest,
  })
  const [saveState, setSaveState] = React.useState<
    'idle' | 'saving' | 'conflict' | 'error'
  >('idle')
  const [saveError, setSaveError] = React.useState<string | null>(null)
  const existing =
    workspace.kernels.state === 'available'
      ? workspace.kernels.items.find((item) => item.path === path)
      : undefined
  const kernelRef = React.useRef<KernelView | null>(existing ?? null)
  const [phase, setPhase] = React.useState<KernelPhase>(
    workspace.kernels.state === 'disabled'
      ? 'disabled'
      : existing
        ? 'idle'
        : 'off',
  )
  const [kernelMessage, setKernelMessage] = React.useState<string | null>(
    workspace.kernels.state === 'disabled' ? workspace.kernels.message : null,
  )
  const [states, setStates] = React.useState<Record<string, CellState>>({})
  const [variables, setVariables] = React.useState<Array<KernelVariable>>([])
  const [history, setHistory] = React.useState<Array<HistoryEntry>>([])
  const [selected, setSelected] = React.useState<string | null>(
    cells.find((cell) => cell.kind !== 'markdown')?.id ?? null,
  )
  const [runningAll, setRunningAll] = React.useState(false)
  const seq = React.useRef(0)
  const stopAll = React.useRef(false)
  const cellsRef = React.useRef(cells)
  cellsRef.current = cells

  const dirty = !sameCells(cells, saved.cells)
  const canRun = phase !== 'disabled'
  const stale = staleCells(cells, states)
  const reads = [
    ...new Set(
      cells
        .filter((cell) => cell.kind === 'sql' && cell.engine !== 'duckdb')
        .flatMap((cell) => sqlReads(cell.source)),
    ),
  ]
  const producedBy = new Map(
    cells
      .filter((cell) => cell.name && states[cell.id]?.status === 'ok')
      .map((cell) => [cell.name as string, cell]),
  )

  useBlocker({
    shouldBlockFn: ({ next }) =>
      dirty &&
      !(next.routeId === '/_app/notebooks' && next.search.path === path) &&
      !window.confirm('Leave this notebook without saving your changes?'),
    enableBeforeUnload: () => dirty,
  })

  const save = React.useCallback(async () => {
    const snapshot = cellsRef.current
    setSaveState('saving')
    setSaveError(null)
    try {
      const result = await saveNotebook({
        data: {
          env,
          path,
          cells: snapshot,
          expected_digest: saved.digest,
        },
      })
      if (result.state === 'conflict') return setSaveState('conflict')
      setSaved({ cells: snapshot, digest: result.document.digest })
      setSaveState('idle')
    } catch (error) {
      setSaveState('error')
      setSaveError(error instanceof Error ? error.message : 'Save failed.')
    }
  }, [env, path, saved.digest])

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (dirty) void save()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dirty, save])

  const ensureKernel = async (): Promise<string | null> => {
    if (kernelRef.current) return kernelRef.current.id
    setPhase('starting')
    try {
      const started = await startKernel({ data: { env, path } })
      if (started.state === 'ok') {
        kernelRef.current = started.data
        setPhase('idle')
        setKernelMessage(null)
        return started.data.id
      }
      if (started.state === 'disabled') {
        setPhase('disabled')
        setKernelMessage(started.message)
      } else {
        setPhase('off')
        setKernelMessage(
          started.state === 'busy'
            ? 'Every kernel slot is in use. Stop a kernel in another notebook, or wait for idle ones to close.'
            : 'The kernel could not start.',
        )
      }
    } catch (error) {
      setPhase('off')
      setKernelMessage(
        error instanceof Error ? error.message : 'The kernel could not start.',
      )
    }
    return null
  }

  /** Calls the kernel, starting a fresh one once if it stopped while idle. */
  const withKernel = async <T,>(
    call: (kernel: string) => Promise<KernelCall<T>>,
  ): Promise<T | string> => {
    for (let attempt = 0; attempt < 2; attempt++) {
      const kernel = await ensureKernel()
      if (!kernel) return 'The kernel is not running.'
      setPhase('busy')
      let result: KernelCall<T>
      try {
        result = await call(kernel)
      } finally {
        setPhase('idle')
      }
      if (result.state === 'ok') return result.data
      if (result.state === 'busy')
        return 'The kernel is still running another cell.'
      if (result.state === 'disabled') {
        kernelRef.current = null
        setPhase('disabled')
        setKernelMessage(result.message)
        return result.message
      }
      kernelRef.current = null
      setPhase('off')
      setKernelMessage(
        'The kernel had stopped, so a new one started. Earlier results were cleared.',
      )
      setStates({})
    }
    return 'The kernel is not running.'
  }

  const refreshVariables = async () => {
    const kernel = kernelRef.current?.id
    if (!kernel) return
    try {
      const result = await getKernelVariables({ data: { env, kernel } })
      if (result.state === 'ok') setVariables(result.data.items)
    } catch {
      // Variables are a convenience; a failed refresh keeps the last list.
    }
  }

  const lakehouseQuery = async (
    sql: string,
  ): Promise<{ id: string; capped: boolean } | string> => {
    let session = await submitQuery({ data: { env, sql, engine: 'trino' } })
    const deadline = Date.now() + QUERY_DEADLINE_MS
    while (!['completed', 'failed', 'cancelled'].includes(session.status)) {
      if (Date.now() > deadline)
        return 'The lakehouse query took longer than 5 minutes.'
      await new Promise((resolve) => setTimeout(resolve, POLL_MS))
      session = await getQuerySession({ data: { env, id: session.id } })
    }
    if (session.status !== 'completed')
      return session.error ?? `The lakehouse query was ${session.status}.`
    return { id: session.id, capped: session.result?.has_more ?? false }
  }

  const runCell = async (id: string): Promise<boolean> => {
    const cell = cellsRef.current.find((item) => item.id === id)
    if (!cell || cell.kind === 'markdown') return true
    const mySeq = ++seq.current
    const label = cell.name ?? cellLabel(cell)
    setStates((current) => ({
      ...current,
      [id]: {
        ...current[id],
        status: 'running',
        seq: mySeq,
        at: Date.now(),
        message: undefined,
      },
    }))
    let outcome: CellRun | string
    let capped: boolean | undefined
    try {
      if (cell.kind === 'python')
        outcome = await withKernel((kernel) =>
          runPython({
            data: { env, kernel, code: cell.source, name: cell.name },
          }),
        )
      else if (!cell.name) outcome = 'Give this SQL cell a result name first.'
      else if (cell.engine === 'duckdb') {
        const name = cell.name
        outcome = await withKernel((kernel) =>
          runDuckdb({ data: { env, kernel, sql: cell.source, name } }),
        )
      } else {
        const name = cell.name
        const query = await lakehouseQuery(cell.source)
        if (typeof query === 'string') outcome = query
        else {
          capped = query.capped
          outcome = await withKernel((kernel) =>
            bindQueryResult({
              data: { env, kernel, query_id: query.id, name },
            }),
          )
        }
      }
    } catch (error) {
      outcome = error instanceof Error ? error.message : 'The cell failed.'
    }
    const at = Date.now()
    const next: CellState =
      typeof outcome === 'string'
        ? { status: 'error', seq: mySeq, at, message: outcome }
        : { status: outcome.status, seq: mySeq, at, run: outcome, capped }
    setStates((current) => ({ ...current, [id]: next }))
    setHistory((current) =>
      [
        {
          key: mySeq,
          cell: id,
          label,
          status: next.status,
          ms: next.run?.duration_ms,
          at,
        },
        ...current,
      ].slice(0, 100),
    )
    void refreshVariables()
    return next.status === 'ok'
  }

  const runAll = async () => {
    setRunningAll(true)
    stopAll.current = false
    for (const cell of cellsRef.current) {
      if (stopAll.current) break
      if (cell.kind === 'markdown') continue
      setSelected(cell.id)
      if (!(await runCell(cell.id))) break
    }
    setRunningAll(false)
  }

  const startNow = async () => {
    setKernelMessage(null)
    const kernel = await ensureKernel()
    if (kernel) void refreshVariables()
  }

  const kernelAction = async (action: 'interrupt' | 'restart' | 'stop') => {
    stopAll.current = true
    const kernel = kernelRef.current?.id
    if (!kernel) return
    try {
      await controlKernel({ data: { env, kernel, action } })
    } catch (error) {
      setKernelMessage(
        error instanceof Error ? error.message : 'The kernel did not respond.',
      )
    }
    if (action !== 'interrupt') {
      setStates({})
      setVariables([])
      if (action === 'stop') {
        kernelRef.current = null
        setPhase('off')
      }
    }
  }

  const patchCell = (id: string, patch: Partial<NotebookCell>) =>
    setCells((current) =>
      current.map((cell) => (cell.id === id ? { ...cell, ...patch } : cell)),
    )
  const moveCell = (id: string, by: -1 | 1) =>
    setCells((current) => {
      const index = current.findIndex((cell) => cell.id === id)
      const target = index + by
      if (index < 0 || target < 0 || target >= current.length) return current
      const next = [...current]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  const addCell = (kind: NotebookCell['kind']) => {
    const cell = newCell(cellsRef.current, kind)
    setCells((current) => {
      const at = current.findIndex((item) => item.id === selected)
      const next = [...current]
      next.splice(at < 0 ? next.length : at + 1, 0, cell)
      return next
    })
    setSelected(cell.id)
    // Wait for the editor to mount, then put the caret in it.
    setTimeout(() => {
      const element = globalThis.document.getElementById(`cell-${cell.id}`)
      element?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      element?.querySelector<HTMLElement>('.cm-content')?.focus()
    }, 50)
  }

  const queryTable = (name: string, table: string) => {
    const stem = table
      .toLowerCase()
      .replace(/[^a-z0-9_]/g, '_')
      .replace(/^[^a-z_]/, '_$&')
    const cell: NotebookCell = {
      id: newCellId(),
      kind: 'sql',
      source: `SELECT *\nFROM ${name}\nLIMIT 100`,
      name: freeName(cellsRef.current, stem.slice(0, 56) || 'table'),
      engine: 'trino',
    }
    cellsRef.current = [...cellsRef.current, cell]
    setCells(cellsRef.current)
    setSelected(cell.id)
    setTimeout(() => {
      globalThis.document
        .getElementById(`cell-${cell.id}`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }, 50)
    if (canRun) void runCell(cell.id)
  }
  const explorer = (
    <NotebookSidebar
      files={
        <NotebookExplorer
          env={env}
          paths={workspace.list.items.map((item) => item.path)}
          active={path}
          dirty={dirty}
          truncated={workspace.list.truncated}
          reads={reads}
          onNew={onNew}
        />
      }
      catalog={<CatalogBrowser env={env} onQuery={queryTable} />}
    />
  )
  const busy = phase === 'busy' || phase === 'starting' || runningAll

  return (
    <>
      <header className="flex min-h-[52px] shrink-0 flex-wrap items-center gap-x-2.5 gap-y-2 border-b border-line px-4 py-2.5 lg:pl-5">
        <h1 className="mr-2 text-sm font-medium">Notebooks</h1>
        <span className="inline-flex h-7 min-w-0 items-center gap-2 rounded-md bg-soft px-2.5 text-[13px] text-foreground">
          <span className="truncate">{fileName(path)}</span>
          {dirty ? (
            <span
              aria-label="Unsaved changes"
              className="size-1.5 rounded-full bg-text-3"
            />
          ) : null}
        </span>
        <Button
          variant="ghost"
          size="sm"
          aria-label="New notebook"
          className="ml-auto lg:ml-0"
          onClick={onNew}
        >
          <PlusIcon />
          <span className="max-sm:hidden">Notebook</span>
        </Button>
        <div className="flex w-full flex-wrap items-center gap-2 lg:ml-auto lg:w-auto lg:justify-end">
          <Popover>
            <PopoverTrigger
              className={cn(
                buttonVariants({ variant: 'outline', size: 'sm' }),
                'lg:hidden',
              )}
            >
              <FilesIcon /> Browse
            </PopoverTrigger>
            <PopoverContent className="max-h-[70dvh] w-[300px] overflow-y-auto p-2.5">
              {explorer}
            </PopoverContent>
          </Popover>
          <span
            title="Lakehouse SQL cells read this environment's configured Nessie ref"
            className={cn(
              buttonVariants({ variant: 'outline', size: 'sm' }),
              'hidden md:inline-flex',
            )}
          >
            <GitBranchIcon className="text-branch" />
            <Mono>{document.nessie_ref}</Mono>
          </span>
          <KernelMenu
            env={env}
            phase={phase}
            kernelId={kernelRef.current?.id ?? null}
            onStart={() => void startNow()}
            onRestart={() => void kernelAction('restart')}
            onInterrupt={() => void kernelAction('interrupt')}
            onStop={() => void kernelAction('stop')}
          />
          <NotebookActions
            phase={phase}
            dirty={dirty}
            saving={saveState === 'saving'}
            canRunAll={canRun && !busy}
            onKernel={(action) => void kernelAction(action)}
            onSave={() => void save()}
            onRunAll={() => void runAll()}
          />
        </div>
      </header>
      <div className="flex min-h-[36px] shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-1.5 text-xs text-muted-foreground lg:pl-5">
        <Mono className="text-text-2">notebooks/{path}</Mono>
        <SaveStatus
          state={saveState}
          dirty={dirty}
          error={saveError}
          onReload={() => void router.invalidate()}
        />
        <span className="hidden items-center gap-1.5 md:inline-flex">
          <LockIcon className="size-3" /> Reads the lakehouse only. Nothing here
          writes to it.
        </span>
        <span className="ml-auto hidden items-center gap-1.5 lg:inline-flex">
          Run cell
          <kbd className="rounded border border-border bg-raised px-1 font-mono text-[11px]">
            ⇧↵
          </kbd>
        </span>
      </div>
      <KernelNotice message={kernelMessage} disabled={phase === 'disabled'} />
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label="Notebook files"
          className="hidden w-[240px] shrink-0 overflow-y-auto border-r border-line p-2.5 lg:block"
        >
          {explorer}
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <section
            aria-label={`Notebook ${fileName(path)}`}
            className="min-h-0 flex-1 overflow-y-auto"
          >
            <div className="mx-auto flex max-w-[880px] flex-col gap-3 px-3 py-5 sm:px-6">
              {cells.map((cell, index) => (
                <NotebookCellView
                  key={cell.id}
                  cell={cell}
                  index={index}
                  count={cells.length}
                  state={states[cell.id]}
                  staleFrom={cells.find(
                    (item) => item.id === stale.get(cell.id),
                  )}
                  selected={selected === cell.id}
                  canRun={canRun}
                  actions={{
                    change: (patch) => patchCell(cell.id, patch),
                    run: () => void runCell(cell.id),
                    move: (by) => moveCell(cell.id, by),
                    remove: () =>
                      setCells((current) =>
                        current.filter((item) => item.id !== cell.id),
                      ),
                    select: () => setSelected(cell.id),
                  }}
                />
              ))}
              <div className="flex justify-center gap-2 py-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => addCell('sql')}
                >
                  + SQL
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => addCell('python')}
                >
                  + Python
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => addCell('markdown')}
                >
                  + Text
                </Button>
              </div>
            </div>
          </section>
          <KernelTools
            variables={variables}
            producedBy={producedBy}
            history={history}
          />
        </div>
        <NotebookOutline
          cells={cells}
          states={states}
          stale={stale}
          selected={selected}
          onSelect={setSelected}
          nessieRef={document.nessie_ref}
          reads={reads}
        />
      </div>
    </>
  )
}

function SaveStatus({
  state,
  dirty,
  error,
  onReload,
}: {
  state: 'idle' | 'saving' | 'conflict' | 'error'
  dirty: boolean
  error: string | null
  onReload: () => void
}) {
  if (state === 'conflict')
    return (
      <span
        role="alert"
        className="inline-flex items-center gap-1.5 text-bad-text"
      >
        Someone changed this file since you opened it.
        <button
          type="button"
          onClick={onReload}
          className="text-link underline underline-offset-2"
        >
          Reload it
        </button>
        (your edits here will be lost)
      </span>
    )
  if (state === 'error')
    return (
      <span role="alert" className="text-bad-text">
        Not saved. {error}
      </span>
    )
  if (state === 'saving') return <span>Saving…</span>
  return <span>{dirty ? 'Unsaved changes · ⌘S to save' : 'Saved'}</span>
}

const th =
  'sticky top-0 h-8 border-b border-line bg-card px-3 text-left text-xs font-normal whitespace-nowrap text-muted-foreground'
const td = 'h-8 border-b border-line-soft px-3 whitespace-nowrap'

function VariablesTable({
  variables,
  producedBy,
}: {
  variables: Array<KernelVariable>
  producedBy: Map<string, NotebookCell>
}) {
  if (variables.length === 0)
    return (
      <p className="px-4 py-3 text-[12.5px] text-muted-foreground">
        Nothing in the kernel yet. Run a cell and its results show up here.
      </p>
    )
  return (
    <table
      className="w-full border-separate border-spacing-0 text-[12.5px]"
      aria-label="Kernel variables"
    >
      <thead>
        <tr>
          <th scope="col" className={th}>
            Name
          </th>
          <th scope="col" className={th}>
            Type
          </th>
          <th scope="col" className={th}>
            Shape
          </th>
          <th scope="col" className={th}>
            From
          </th>
          <th scope="col" className={th}>
            Preview
          </th>
        </tr>
      </thead>
      <tbody>
        {variables.map((variable) => {
          const cell = producedBy.get(variable.name)
          return (
            <tr key={variable.name}>
              <td className={cn(td, 'font-mono font-medium text-foreground')}>
                {variable.name}
              </td>
              <td className={cn(td, 'font-mono text-[12px] text-text-2')}>
                {variable.type}
              </td>
              <td className={cn(td, 'text-text-2')}>{variable.shape ?? '–'}</td>
              <td className={cn(td, 'text-text-2')}>
                {cell ? cellLabel(cell) : 'Python'}
              </td>
              <td
                className={cn(
                  td,
                  'max-w-[360px] truncate font-mono text-[12px] text-muted-foreground',
                )}
                title={variable.preview ?? ''}
              >
                {variable.preview}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function RunHistory({ history }: { history: Array<HistoryEntry> }) {
  if (history.length === 0)
    return (
      <p className="px-4 py-3 text-[12.5px] text-muted-foreground">
        No cells have run in this session.
      </p>
    )
  const time = new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
  return (
    <table
      className="w-full border-separate border-spacing-0 text-[12.5px]"
      aria-label="Run history"
    >
      <thead>
        <tr>
          <th scope="col" className={th}>
            Cell
          </th>
          <th scope="col" className={th}>
            Result
          </th>
          <th scope="col" className={th}>
            Took
          </th>
          <th scope="col" className={th}>
            At
          </th>
        </tr>
      </thead>
      <tbody>
        {history.map((entry) => (
          <tr key={entry.key}>
            <td className={cn(td, 'font-mono text-foreground')}>
              <a href={`#cell-${entry.cell}`} className="hover:underline">
                {entry.label}
              </a>
            </td>
            <td className={td}>
              <span className="inline-flex items-center gap-1.5 text-text-2">
                <Dot
                  tone={
                    entry.status === 'ok'
                      ? 'ok'
                      : entry.status === 'running'
                        ? 'neutral'
                        : 'bad'
                  }
                />
                {entry.status === 'ok'
                  ? 'Ran'
                  : entry.status === 'timeout'
                    ? 'Timed out'
                    : 'Failed'}
              </span>
            </td>
            <td className={cn(td, 'text-text-2')}>
              {entry.ms === undefined ? '–' : `${entry.ms} ms`}
            </td>
            <td className={cn(td, 'text-muted-foreground')}>
              {time.format(entry.at)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function outlineTone(
  id: string,
  states: Record<string, CellState>,
  stale: Map<string, string>,
) {
  if (stale.has(id)) return 'warn' as const
  const status = states[id]?.status
  return status === 'ok'
    ? ('ok' as const)
    : status === 'error'
      ? ('bad' as const)
      : ('neutral' as const)
}

function NotebookOutline({
  cells,
  states,
  stale,
  selected,
  onSelect,
  nessieRef,
  reads,
}: {
  cells: Array<NotebookCell>
  states: Record<string, CellState>
  stale: Map<string, string>
  selected: string | null
  onSelect: (id: string) => void
  nessieRef: string
  reads: Array<string>
}) {
  return (
    <aside
      aria-label="Outline"
      className="hidden w-[200px] shrink-0 overflow-y-auto border-l border-line px-3 py-4 xl:block"
    >
      <div className="pb-2 text-[11px] font-medium tracking-wide text-faint uppercase">
        Outline
      </div>
      <ul className="flex flex-col gap-0.5">
        {outline(cells).map((item, index) => (
          <li key={`${item.id}-${index}`}>
            <a
              href={`#cell-${item.id}`}
              onClick={() => onSelect(item.id)}
              className={cn(
                'flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[12.5px] text-text-2 hover:bg-nav-hover hover:text-foreground',
                item.kind === 'cell' && 'pl-3 font-mono text-[12px]',
                item.kind === 'heading' &&
                  item.level === 1 &&
                  'font-medium text-foreground',
                selected === item.id && 'bg-nav-on',
              )}
            >
              {item.kind === 'cell' ? (
                <Dot tone={outlineTone(item.id, states, stale)} />
              ) : null}
              <span className="truncate">{item.text}</span>
            </a>
          </li>
        ))}
      </ul>
      <div className="mt-5 pb-2 text-[11px] font-medium tracking-wide text-faint uppercase">
        This notebook
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1.5 text-[12.5px]">
        <dt className="text-muted-foreground">Kernel</dt>
        <dd className="text-right text-text-2">Python 3</dd>
        <dt className="text-muted-foreground">Data as of</dt>
        <dd className="truncate text-right font-mono text-[12px] text-text-2">
          {nessieRef}
        </dd>
        <dt className="text-muted-foreground">Reads</dt>
        <dd className="text-right text-text-2">
          {reads.length} {reads.length === 1 ? 'table' : 'tables'}
        </dd>
        <dt className="text-muted-foreground">Cells</dt>
        <dd className="text-right text-text-2">{cells.length}</dd>
      </dl>
    </aside>
  )
}

function KernelNotice({
  message,
  disabled,
}: {
  message: string | null
  disabled: boolean
}) {
  if (!message) return null
  return (
    <div
      role="status"
      className={cn(
        'shrink-0 border-b px-4 py-2 text-[12.5px] lg:pl-5',
        disabled
          ? 'border-warn-soft bg-warn-wash text-warn-ink'
          : 'border-line bg-raised text-text-2',
      )}
    >
      {message}
      {disabled ? ' You can still read, edit and save notebooks.' : ''}
    </div>
  )
}

function NotebookActions({
  phase,
  dirty,
  saving,
  canRunAll,
  onKernel,
  onSave,
  onRunAll,
}: {
  phase: KernelPhase
  dirty: boolean
  saving: boolean
  canRunAll: boolean
  onKernel: (action: 'interrupt') => void
  onSave: () => void
  onRunAll: () => void
}) {
  return (
    <>
      {phase === 'busy' ? (
        <Button
          variant="outline"
          size="sm"
          onClick={() => onKernel('interrupt')}
        >
          <SquareIcon className="fill-current" /> Interrupt
        </Button>
      ) : null}
      <Button
        variant="outline"
        size="sm"
        disabled={!dirty || saving}
        onClick={onSave}
      >
        {saving ? 'Saving…' : 'Save'}
      </Button>
      <Button size="sm" disabled={!canRunAll} onClick={onRunAll}>
        <PlayIcon className="fill-current" /> Run all
      </Button>
    </>
  )
}

function KernelTools({
  variables,
  producedBy,
  history,
}: {
  variables: Array<KernelVariable>
  producedBy: Map<string, NotebookCell>
  history: Array<HistoryEntry>
}) {
  const [tool, setTool] = React.useState<'variables' | 'history'>('variables')
  const [open, setOpen] = React.useState(true)
  const Chevron = open ? ChevronDownIcon : ChevronUpIcon
  return (
    <section
      aria-label="Kernel tools"
      className={cn(
        'hidden shrink-0 flex-col border-t border-line lg:flex',
        open && 'h-[188px]',
      )}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <Segmented
          aria-label="Kernel tools"
          value={tool}
          onValueChange={(next) => {
            setTool(next)
            setOpen(true)
          }}
          options={[
            { value: 'variables', label: `Variables · ${variables.length}` },
            { value: 'history', label: 'Run history' },
          ]}
        />
        <span className="ml-auto text-xs text-muted-foreground">
          Updated when a cell finishes
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-expanded={open}
          aria-label={open ? 'Hide kernel tools' : 'Show kernel tools'}
          onClick={() => setOpen((value) => !value)}
        >
          <Chevron />
        </Button>
      </div>
      {open ? (
        <div className="min-h-0 flex-1 overflow-auto">
          {tool === 'variables' ? (
            <VariablesTable variables={variables} producedBy={producedBy} />
          ) : (
            <RunHistory history={history} />
          )}
        </div>
      ) : null}
    </section>
  )
}

function NotebookSidebar({
  files,
  catalog,
}: {
  files: React.ReactNode
  catalog: React.ReactNode
}) {
  const [tab, setTab] = React.useState<'files' | 'catalog'>('files')
  return (
    <div className="flex flex-col gap-2.5">
      <Segmented
        aria-label="Browse"
        className="grid w-full grid-cols-2"
        value={tab}
        onValueChange={setTab}
        options={[
          { value: 'files', label: 'Files' },
          { value: 'catalog', label: 'Catalog' },
        ]}
      />
      {tab === 'files' ? files : catalog}
    </div>
  )
}
