/** Notebook workspace: .ipynb files in the project, run on a Jupyter kernel, drawn by Observatory. */
import * as React from 'react'
import { createFileRoute, useBlocker, useRouter } from '@tanstack/react-router'
import { z } from 'zod'
import { FilesIcon, PlusIcon } from 'lucide-react'
import type {
  CellRun,
  KernelVariable,
  KernelView,
  NotebookDocument,
  SavedOutput,
} from '@/lib/data/api/notebooks'
import type { CellState, NotebookCell } from '@/lib/notebook'
import type { CellWork } from '@/components/notebooks/use-kernel'
import type { FileAction } from '@/components/notebooks/file-dialogs'
import type { HistoryEntry } from '@/components/notebooks/notebook-panels'
import {
  deleteNotebook,
  duplicateNotebook,
  getKernelVariables,
  getNotebookWorkspace,
  moveNotebook,
  notebookPathSchema,
  saveNotebook,
} from '@/lib/data/api/notebooks'
import {
  setNotebookSchedule,
  startNotebookRun,
} from '@/lib/data/api/notebook-publish'
import {
  cellLabel,
  freeName,
  newCell,
  newCellId,
  sourceHash,
  sqlReads,
  staleCells,
  starterCells,
} from '@/lib/notebook'
import {
  dirtyPaths,
  dropDraft,
  keepDraft,
  readDraft,
  readTabs,
  renameTab,
  withTab,
  writeTabs,
} from '@/lib/notebook-tabs'
import { useKernel } from '@/components/notebooks/use-kernel'
import { NotebookCellView } from '@/components/notebooks/cell'
import { NotebookExplorer } from '@/components/notebooks/explorer'
import { CatalogBrowser } from '@/components/notebooks/catalog-browser'
import { NewNotebookDialog } from '@/components/notebooks/new-notebook'
import { NotebookFileDialog } from '@/components/notebooks/file-dialogs'
import { NotebookTabs } from '@/components/notebooks/notebook-tabs'
import {
  NotebookHeader,
  NotebookSubheader,
} from '@/components/notebooks/notebook-header'
import {
  KernelNotice,
  KernelTools,
  NotebookOutline,
  useNotebookRuns,
} from '@/components/notebooks/notebook-panels'
import { PackagesDialog } from '@/components/notebooks/packages-dialog'
import { ScheduleDialog } from '@/components/notebooks/schedule-dialog'
import { GitDialog } from '@/components/notebooks/git-dialog'
import { PromoteDialog } from '@/components/notebooks/promote-dialog'
import {
  pythonCompletion,
  sqlCompletion,
} from '@/components/notebooks/completions'
import { EmptyState, PageSkeleton, RouteError } from '@/components/phlo/states'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/toggle-group'

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
type Dialog = 'packages' | 'schedule' | 'git' | null

function fileName(path: string) {
  return path.split('/').at(-1) ?? path
}

function folderList(paths: ReadonlyArray<string>) {
  return [
    ...new Set(
      paths.flatMap((item) => {
        const parts = item.split('/').slice(0, -1)
        return parts.map((_, index) => parts.slice(0, index + 1).join('/'))
      }),
    ),
  ].sort()
}

function failure(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function linkTo(env: string, path: string) {
  const url = new URL(globalThis.location.href)
  url.search = new URLSearchParams({ env, path }).toString()
  url.hash = ''
  return url.toString()
}

/** New, rename, duplicate and delete, shared by the empty page and the workspace. */
function useFileActions(env: string, active: string | undefined) {
  const router = useRouter()
  const navigate = Route.useNavigate()
  const [action, setAction] = React.useState<FileAction | null>(null)
  const create = async (path: string) => {
    try {
      const saved = await saveNotebook({
        data: {
          env,
          path,
          cells: starterCells(),
          packages: [],
          expected_digest: null,
        },
      })
      if (saved.state === 'conflict') return 'A notebook with that name exists.'
      if (saved.state === 'invalid') return saved.message
      // Opening it loads the list again, so the dialog stays busy until the
      // notebook is on screen instead of closing onto the old page.
      await navigate({ search: { env, path } })
      return null
    } catch (error) {
      return failure(error, 'Could not create it.')
    }
  }
  const move = async (from: string, to: string) => {
    const draft = readDraft(env, from)
    try {
      const result = await moveNotebook({
        data: { env, path: from, to, expected_digest: draft?.saved.digest },
      })
      if (result.state === 'refused') return result.message
      renameTab(env, from, to)
      if (from === active) await navigate({ search: { env, path: to } })
      else await router.invalidate()
      return null
    } catch (error) {
      return failure(error, 'Could not rename it.')
    }
  }
  const duplicate = async (from: string, to: string) => {
    try {
      const result = await duplicateNotebook({ data: { env, path: from, to } })
      if (result.state === 'refused') return result.message
      await navigate({ search: { env, path: to } })
      return null
    } catch (error) {
      return failure(error, 'Could not duplicate it.')
    }
  }
  const remove = async (path: string) => {
    try {
      const result = await deleteNotebook({
        data: { env, path, digest: readDraft(env, path)?.saved.digest },
      })
      if (result.state === 'refused') return result.message
      dropDraft(env, path)
      writeTabs(
        env,
        readTabs(env).filter((item) => item !== path),
      )
      if (path === active) void navigate({ search: { env } })
      else await router.invalidate()
      return null
    } catch (error) {
      return failure(error, 'Could not delete it.')
    }
  }
  const act = (next: FileAction | { kind: 'link'; path: string }) => {
    if (next.kind === 'link')
      void navigator.clipboard.writeText(linkTo(env, next.path))
    else setAction(next)
  }
  const dialog = (
    <NotebookFileDialog
      action={action}
      onClose={() => setAction(null)}
      onMove={move}
      onDuplicate={duplicate}
      onDelete={remove}
    />
  )
  return { create, act, dialog }
}

function NotebooksPage() {
  const data = Route.useLoaderData()
  const { env } = Route.useSearch()
  const { environments } = Route.useRouteContext()
  const [creating, setCreating] = React.useState(false)
  const active =
    data.notebook.state === 'open'
      ? data.notebook.document.path
      : data.notebook.state === 'unreadable'
        ? data.notebook.path
        : undefined
  const files = useFileActions(env, active)
  const paths = data.list.items.map((item) => item.path)
  const dialogs = (
    <>
      <NewNotebookDialog
        open={creating}
        onOpenChange={setCreating}
        folders={folderList(paths)}
        onCreate={files.create}
      />
      {files.dialog}
    </>
  )
  if (data.notebook.state === 'open')
    return (
      <>
        <NotebookWorkspace
          key={`${env}:${data.notebook.document.path}`}
          workspace={data}
          document={data.notebook.document}
          outputs={data.notebook.outputs.items}
          environments={environments.map((item) => item.env)}
          onNew={() => setCreating(true)}
          onFile={files.act}
        />
        {dialogs}
      </>
    )
  return (
    <>
      <EmptyPage
        workspace={data}
        onNew={() => setCreating(true)}
        onFile={files.act}
      />
      {dialogs}
    </>
  )
}

function EmptyPage({
  workspace,
  onNew,
  onFile,
}: {
  workspace: Workspace
  onNew: () => void
  onFile: (action: FileAction | { kind: 'link'; path: string }) => void
}) {
  const { env } = Route.useSearch()
  const notebook = workspace.notebook
  return (
    <>
      <header className="flex min-h-[52px] shrink-0 items-center border-b border-line px-4 py-2.5 lg:pl-5">
        <h1 className="text-sm font-medium">Notebooks</h1>
        <Button size="sm" className="ml-auto" onClick={onNew}>
          <PlusIcon /> Notebook
        </Button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside
          aria-label="Notebook files"
          className="shrink-0 border-b border-line p-2.5 lg:w-[240px] lg:overflow-y-auto lg:border-r lg:border-b-0"
        >
          <NotebookExplorer
            env={env}
            paths={workspace.list.items.map((item) => item.path)}
            active={notebook.state === 'unreadable' ? notebook.path : undefined}
            dirty={false}
            truncated={workspace.list.truncated}
            reads={[]}
            onNew={onNew}
            onAction={onFile}
          />
        </aside>
        <div className="flex min-w-0 flex-1 items-start justify-center overflow-y-auto p-6">
          {notebook.state === 'unreadable' ? (
            <EmptyState title={`Cannot open ${fileName(notebook.path)}`}>
              {notebook.message}
            </EmptyState>
          ) : (
            <EmptyState
              title="No notebooks yet"
              className="max-w-md"
              icon={<FilesIcon className="size-3" />}
              action={
                <Button size="sm" onClick={onNew}>
                  <PlusIcon /> New notebook
                </Button>
              }
            >
              Notebooks are .ipynb files in the project&apos;s{' '}
              <code className="font-mono">{workspace.list.root}</code> folder.
              Start one here, or add one in git.
            </EmptyState>
          )}
        </div>
      </div>
    </>
  )
}

/** Each cell's kept output from an earlier session, as the starting state. */
function savedStates(
  outputs: ReadonlyArray<SavedOutput>,
  cells: ReadonlyArray<NotebookCell>,
): Record<string, CellState> {
  const ids = new Set(cells.map((cell) => cell.id))
  const states: Record<string, CellState> = {}
  for (const output of outputs) {
    if (!ids.has(output.cell_id) || output.run.status === 'running') continue
    states[output.cell_id] = {
      status: output.run.status,
      seq: 0,
      at: Date.parse(output.finished_at),
      run: output.run,
      saved: { actor: output.actor, sourceSha256: output.source_sha256 },
    }
  }
  return states
}

/** Cells whose kept output was made from source that has changed since. */
function useEditedCells(
  cells: ReadonlyArray<NotebookCell>,
  states: Record<string, CellState>,
) {
  const [edited, setEdited] = React.useState<ReadonlySet<string>>(new Set())
  const key = cells
    .map(
      (cell) =>
        `${cell.id}:${states[cell.id]?.saved?.sourceSha256 ?? ''}:${cell.source}`,
    )
    .join('\u0000')
  React.useEffect(() => {
    let live = true
    const kept = cells.filter((cell) => states[cell.id]?.saved)
    void Promise.all(
      kept.map(async (cell) =>
        (await sourceHash(cell.source)) === states[cell.id]?.saved?.sourceSha256
          ? null
          : cell.id,
      ),
    ).then((ids) => {
      if (live)
        setEdited(new Set(ids.filter((id): id is string => id !== null)))
    })
    return () => {
      live = false
    }
  }, [key])
  return edited
}

/** The open notebook's cells and packages, the saved version, and saving. */
function useDraft(
  env: string,
  document: NotebookDocument,
  initial: Record<string, CellState>,
) {
  const path = document.path
  const [draft] = React.useState(() => {
    const kept = readDraft(env, path)
    // A kept draft wins only while it still belongs to the file on disk.
    if (kept && kept.saved.digest === document.digest) return kept
    return {
      cells: document.cells,
      packages: document.packages,
      saved: {
        cells: document.cells,
        packages: document.packages,
        digest: document.digest,
      },
      states: initial,
    }
  })
  const [cells, setCells] = React.useState<Array<NotebookCell>>(draft.cells)
  const [packages, setPackages] = React.useState<Array<string>>(draft.packages)
  const [saved, setSaved] = React.useState(draft.saved)
  const [saveState, setSaveState] = React.useState<
    'idle' | 'saving' | 'conflict' | 'error'
  >('idle')
  const [saveError, setSaveError] = React.useState<string | null>(null)
  const cellsRef = React.useRef(cells)
  cellsRef.current = cells
  const dirty =
    JSON.stringify(cells) !== JSON.stringify(saved.cells) ||
    JSON.stringify(packages) !== JSON.stringify(saved.packages)

  const save = React.useCallback(
    async (nextPackages = packages): Promise<string | null> => {
      const snapshot = cellsRef.current
      setSaveState('saving')
      setSaveError(null)
      try {
        const result = await saveNotebook({
          data: {
            env,
            path,
            cells: snapshot,
            packages: nextPackages,
            expected_digest: saved.digest,
          },
        })
        if (result.state === 'conflict') {
          setSaveState('conflict')
          return 'Someone changed this file since you opened it.'
        }
        if (result.state === 'invalid') {
          setSaveState('error')
          setSaveError(result.message)
          return result.message
        }
        setPackages(result.document.packages)
        setSaved({
          cells: snapshot,
          packages: result.document.packages,
          digest: result.document.digest,
        })
        setSaveState('idle')
        return null
      } catch (error) {
        const message = failure(error, 'Save failed.')
        setSaveState('error')
        setSaveError(message)
        return message
      }
    },
    [env, path, packages, saved.digest],
  )
  return {
    draft,
    cells,
    setCells,
    cellsRef,
    packages,
    saved,
    setSaved,
    dirty,
    save,
    saveState,
    saveError,
  }
}

function workFor(cell: NotebookCell): CellWork | string {
  if (cell.kind === 'python')
    return {
      cellId: cell.id,
      kind: 'python',
      source: cell.source,
      name: cell.name,
    }
  if (!cell.name) return 'Give this SQL cell a result name first.'
  if (!cell.source.trim()) return 'Write some SQL first.'
  return {
    cellId: cell.id,
    kind: cell.engine === 'duckdb' ? 'duckdb' : 'trino',
    source: cell.source,
    name: cell.name,
  }
}

function capped(run: CellRun) {
  return run.outputs.some(
    (output) =>
      output.name === 'stderr' && output.text?.startsWith('Only the first'),
  )
}

/** Runs cells one at a time on the kernel and keeps each cell's latest state. */
function useCellRuns({
  env,
  kernel,
  cellsRef,
  initial,
}: {
  env: string
  kernel: ReturnType<typeof useKernel>
  cellsRef: React.RefObject<Array<NotebookCell>>
  initial: Record<string, CellState>
}) {
  const [states, setStates] = React.useState<Record<string, CellState>>(initial)
  const [variables, setVariables] = React.useState<Array<KernelVariable>>([])
  const [history, setHistory] = React.useState<Array<HistoryEntry>>([])
  const [runningAll, setRunningAll] = React.useState(false)
  const seq = React.useRef(0)
  const stopAll = React.useRef(false)

  const refreshVariables = React.useCallback(async () => {
    const id = kernel.kernelId()
    if (!id) return
    try {
      const result = await getKernelVariables({ data: { env, kernel: id } })
      if (result.state === 'ok') setVariables(result.data.items)
    } catch {
      // Variables are a convenience; a failed refresh keeps the last list.
    }
  }, [env, kernel])

  const runCell = async (id: string): Promise<boolean> => {
    const cell = cellsRef.current.find((item) => item.id === id)
    if (!cell || cell.kind === 'markdown') return true
    const mySeq = ++seq.current
    const set = (next: CellState) =>
      setStates((current) => ({ ...current, [id]: next }))
    set({ status: 'running', seq: mySeq, at: Date.now() })
    const work = workFor(cell)
    let outcome: CellRun | string
    try {
      outcome =
        typeof work === 'string'
          ? work
          : await kernel.run(work, (run) =>
              set({ status: 'running', seq: mySeq, at: Date.now(), run }),
            )
    } catch (error) {
      outcome = failure(error, 'The cell failed.')
    }
    const at = Date.now()
    const next: CellState =
      typeof outcome === 'string'
        ? { status: 'error', seq: mySeq, at, message: outcome }
        : {
            status: outcome.status === 'running' ? 'error' : outcome.status,
            seq: mySeq,
            at,
            run: outcome,
            capped: capped(outcome),
          }
    set(next)
    setHistory((current) =>
      [
        {
          key: mySeq,
          cell: id,
          label: cell.name ?? cellLabel(cell),
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

  const runAll = async (select: (id: string) => void) => {
    setRunningAll(true)
    stopAll.current = false
    for (const cell of cellsRef.current) {
      if (stopAll.current) break
      if (cell.kind === 'markdown') continue
      select(cell.id)
      if (!(await runCell(cell.id))) break
    }
    setRunningAll(false)
  }

  const reset = () => {
    setStates({})
    setVariables([])
  }
  return {
    states,
    variables,
    history,
    runningAll,
    runCell,
    runAll,
    stop: () => {
      stopAll.current = true
    },
    reset,
    refreshVariables,
  }
}

function existingKernel(workspace: Workspace, path: string) {
  if (workspace.kernels.state !== 'available') return undefined
  return workspace.kernels.items.find((item: KernelView) => item.path === path)
}

/** What the cells read and produce, and which results are stale. */
function derive(
  cells: ReadonlyArray<NotebookCell>,
  states: Record<string, CellState>,
) {
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
  return { stale: staleCells(cells, states), reads, producedBy }
}

function NotebookWorkspace({
  workspace,
  document,
  outputs,
  environments,
  onNew,
  onFile,
}: {
  workspace: Workspace
  document: NotebookDocument
  outputs: ReadonlyArray<SavedOutput>
  environments: Array<string>
  onNew: () => void
  onFile: (action: FileAction | { kind: 'link'; path: string }) => void
}) {
  const { env } = Route.useSearch()
  const router = useRouter()
  const path = document.path
  const access = document.access
  const [initial] = React.useState(() => savedStates(outputs, document.cells))
  const notebook = useDraft(env, document, initial)
  const { cells, setCells, cellsRef, dirty } = notebook
  const [schedule, setSchedule] = React.useState(document.schedule)
  const [dialog, setDialog] = React.useState<Dialog>(null)
  const [promoting, setPromoting] = React.useState<NotebookCell | null>(null)
  const [selected, setSelected] = React.useState<string | null>(
    cells.find((cell) => cell.kind !== 'markdown')?.id ?? null,
  )
  const existing = existingKernel(workspace, path)
  const resetRef = React.useRef<() => void>(() => undefined)
  const kernel = useKernel({
    env,
    path,
    existing,
    disabled:
      workspace.kernels.state === 'disabled' ? workspace.kernels.message : null,
    onLost: () => resetRef.current(),
  })
  const runs = useCellRuns({
    env,
    kernel,
    cellsRef,
    initial: notebook.draft.states,
  })
  resetRef.current = runs.reset
  const notebookRuns = useNotebookRuns(env, path)
  const edited = useEditedCells(cells, runs.states)

  // Keep this tab's work so switching notebooks does not lose it.
  React.useEffect(() => {
    keepDraft(env, path, {
      cells,
      packages: notebook.packages,
      saved: notebook.saved,
      states: Object.fromEntries(
        Object.entries(runs.states).filter(
          ([, state]) => state.status !== 'running',
        ),
      ),
    })
  }, [env, path, cells, notebook.packages, notebook.saved, runs.states])
  React.useEffect(() => {
    writeTabs(env, withTab(readTabs(env), path))
  }, [env, path])

  useBlocker({
    shouldBlockFn: ({ next }) =>
      next.routeId !== '/_app/notebooks' &&
      dirtyPaths(env).length > 0 &&
      !window.confirm('Leave notebooks without saving your changes?'),
    enableBeforeUnload: () => dirtyPaths(env).length > 0,
  })

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (dirty && access.edit) void notebook.save()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dirty, access.edit, notebook])

  const canRun = access.run && kernel.phase !== 'disabled'
  const runBlocked = access.run
    ? (kernel.message ?? undefined)
    : 'You can read this notebook but not run it.'
  const { stale, reads, producedBy } = derive(cells, runs.states)
  const completions = React.useMemo(
    () => ({
      sql: sqlCompletion(env, () =>
        cellsRef.current.flatMap((cell) => (cell.name ? [cell.name] : [])),
      ),
      python: pythonCompletion(env, kernel.kernelId),
    }),
    [env, cellsRef, kernel.kernelId],
  )

  const kernelAction = async (action: 'interrupt' | 'restart' | 'stop') => {
    runs.stop()
    await kernel.control(action)
    if (action !== 'interrupt') runs.reset()
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
  const focusCell = (id: string, edit: boolean) =>
    setTimeout(() => {
      const element = globalThis.document.getElementById(`cell-${id}`)
      element?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      if (edit) element?.querySelector<HTMLElement>('.cm-content')?.focus()
    }, 50)
  const addCell = (kind: NotebookCell['kind']) => {
    const cell = newCell(cellsRef.current, kind)
    setCells((current) => {
      const at = current.findIndex((item) => item.id === selected)
      const next = [...current]
      next.splice(at < 0 ? next.length : at + 1, 0, cell)
      return next
    })
    setSelected(cell.id)
    focusCell(cell.id, true)
  }
  const queryTable = (name: string, table: string) => {
    if (!access.edit) return
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
    focusCell(cell.id, false)
    if (canRun) void runs.runCell(cell.id)
  }

  const saveSchedule = async (change: {
    cron: string | null
    runEnv: string
    enabled: boolean
  }) => {
    try {
      const result = await setNotebookSchedule({
        data: {
          env,
          path,
          cron: change.cron,
          run_env: change.runEnv,
          enabled: change.enabled,
          expected_digest: notebook.saved.digest,
        },
      })
      if (result.state === 'refused') return result.message
      setSchedule(result.data.schedule)
      notebook.setSaved((current) => ({
        ...current,
        digest: result.data.digest,
      }))
      notebookRuns.refresh()
      return null
    } catch (error) {
      return failure(error, 'The schedule was not saved.')
    }
  }
  const runInBackground = async () => {
    try {
      const result = await startNotebookRun({ data: { env, path } })
      if (result.state === 'refused') kernel.setMessage(result.message)
    } catch (error) {
      kernel.setMessage(failure(error, 'The run did not start.'))
    }
    notebookRuns.refresh()
  }

  const sidebar = (
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
          onAction={onFile}
          editable={access.edit}
        />
      }
      catalog={
        <CatalogBrowser
          env={env}
          environments={environments}
          onQuery={queryTable}
        />
      }
    />
  )

  return (
    <>
      <NotebookHeader
        path={path}
        nessieRef={document.nessie_ref}
        dirty={dirty}
        access={access}
        sidebar={sidebar}
        phase={kernel.phase}
        kernelId={kernel.kernel?.id ?? null}
        env={env}
        saving={notebook.saveState === 'saving'}
        canRunAll={canRun && !runs.runningAll && kernel.phase !== 'busy'}
        hasSchedule={schedule !== null}
        packageCount={notebook.packages.length}
        onNew={onNew}
        onKernel={(action) => void kernelAction(action)}
        onStart={() => {
          void kernel.start().then((started) => {
            if ('id' in started) void runs.refreshVariables()
          })
        }}
        onSave={() => void notebook.save()}
        onRunAll={() => void runs.runAll(setSelected)}
        onDialog={setDialog}
        onFile={onFile}
      />
      <NotebookTabs
        env={env}
        active={path}
        known={workspace.list.items.map((item) => item.path)}
      />
      <NotebookSubheader
        path={path}
        access={access}
        saveState={notebook.saveState}
        saveError={notebook.saveError}
        dirty={dirty}
        onReload={() => {
          dropDraft(env, path)
          void router.invalidate()
        }}
      />
      <KernelNotice
        message={kernel.message}
        disabled={kernel.phase === 'disabled'}
      />
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label="Notebook files"
          className="hidden w-[240px] shrink-0 overflow-y-auto border-r border-line p-2.5 lg:block"
        >
          {sidebar}
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
                  state={runs.states[cell.id]}
                  staleFrom={cells.find(
                    (item) => item.id === stale.get(cell.id),
                  )}
                  selected={selected === cell.id}
                  canRun={canRun}
                  runBlocked={runBlocked}
                  editable={access.edit}
                  edited={edited.has(cell.id)}
                  complete={
                    cell.kind === 'sql' ? completions.sql : completions.python
                  }
                  actions={{
                    change: (patch) => patchCell(cell.id, patch),
                    run: () => void runs.runCell(cell.id),
                    move: (by) => moveCell(cell.id, by),
                    remove: () =>
                      setCells((current) =>
                        current.filter((item) => item.id !== cell.id),
                      ),
                    select: () => setSelected(cell.id),
                    promote: access.publish
                      ? () => setPromoting(cell)
                      : undefined,
                  }}
                />
              ))}
              {access.edit ? <AddCells onAdd={addCell} /> : null}
            </div>
          </section>
          <KernelTools
            env={env}
            variables={runs.variables}
            producedBy={producedBy}
            history={runs.history}
            reads={reads}
            runs={{
              runs: notebookRuns.runs,
              cells,
              schedule,
              canRun: access.run && !notebookRuns.running,
              dirty,
              onRun: () => void runInBackground(),
            }}
          />
        </div>
        <NotebookOutline
          cells={cells}
          states={runs.states}
          stale={stale}
          selected={selected}
          onSelect={setSelected}
          nessieRef={document.nessie_ref}
          reads={reads}
          packages={notebook.packages}
          schedule={schedule}
        />
      </div>
      <NotebookDialogs
        env={env}
        path={path}
        dialog={dialog}
        setDialog={setDialog}
        kernel={kernel.kernel}
        packages={notebook.packages}
        editable={access.edit}
        onSavePackages={(next) => notebook.save(next)}
        environments={environments}
        schedule={schedule}
        schedulerOn={notebookRuns.runs?.schedules_enabled ?? false}
        onSaveSchedule={saveSchedule}
        dirty={dirty}
        promoting={promoting}
        onPromoted={() => setPromoting(null)}
      />
    </>
  )
}

function NotebookDialogs({
  env,
  path,
  dialog,
  setDialog,
  kernel,
  packages,
  editable,
  onSavePackages,
  environments,
  schedule,
  schedulerOn,
  onSaveSchedule,
  dirty,
  promoting,
  onPromoted,
}: {
  env: string
  path: string
  dialog: Dialog
  setDialog: (dialog: Dialog) => void
  kernel: KernelView | null
  packages: Array<string>
  editable: boolean
  onSavePackages: (packages: Array<string>) => Promise<string | null>
  environments: Array<string>
  schedule: NotebookDocument['schedule']
  schedulerOn: boolean
  onSaveSchedule: React.ComponentProps<typeof ScheduleDialog>['onSave']
  dirty: boolean
  promoting: NotebookCell | null
  onPromoted: () => void
}) {
  return (
    <>
      <PackagesDialog
        open={dialog === 'packages'}
        onOpenChange={(open) => setDialog(open ? 'packages' : null)}
        env={env}
        kernel={kernel?.status === 'idle' ? kernel.id : null}
        packages={packages}
        editable={editable}
        onSave={onSavePackages}
      />
      <ScheduleDialog
        open={dialog === 'schedule'}
        onOpenChange={(open) => setDialog(open ? 'schedule' : null)}
        env={env}
        environments={environments}
        schedule={schedule}
        schedulerOn={schedulerOn}
        onSave={onSaveSchedule}
      />
      <GitDialog
        open={dialog === 'git'}
        onOpenChange={(open) => setDialog(open ? 'git' : null)}
        env={env}
        path={path}
        dirty={dirty}
      />
      <PromoteDialog
        env={env}
        path={path}
        cell={promoting}
        unsaved={dirty}
        onClose={onPromoted}
      />
    </>
  )
}

function AddCells({ onAdd }: { onAdd: (kind: NotebookCell['kind']) => void }) {
  return (
    <div className="flex justify-center gap-2 py-2">
      <Button variant="outline" size="sm" onClick={() => onAdd('sql')}>
        + SQL
      </Button>
      <Button variant="outline" size="sm" onClick={() => onAdd('python')}>
        + Python
      </Button>
      <Button variant="outline" size="sm" onClick={() => onAdd('markdown')}>
        + Text
      </Button>
    </div>
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
