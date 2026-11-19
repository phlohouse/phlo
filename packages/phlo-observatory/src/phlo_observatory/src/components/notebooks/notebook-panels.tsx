/** The notebook's side and bottom panels: variables, cell history, notebook runs, lineage, outline. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import {
  ChevronDownIcon,
  ChevronUpIcon,
  Loader2Icon,
  PlayIcon,
} from 'lucide-react'
import type { KernelVariable, NotebookSchedule } from '@/lib/data/api/notebooks'
import type {
  LineageAsset,
  NotebookRun,
  NotebookRuns,
} from '@/lib/data/api/notebook-publish'
import type { CellState, NotebookCell } from '@/lib/notebook'
import {
  getNotebookLineage,
  getNotebookRuns,
} from '@/lib/data/api/notebook-publish'
import { cellLabel, describeCron, outline } from '@/lib/notebook'
import { Dot, Mono } from '@/components/phlo/status'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/toggle-group'
import { cn } from '@/lib/utils'

export type HistoryEntry = {
  key: number
  cell: string
  label: string
  status: CellState['status']
  ms?: number
  at: number
}

const th =
  'sticky top-0 h-8 border-b border-line bg-card px-3 text-left text-xs font-normal whitespace-nowrap text-muted-foreground'
const td = 'h-8 border-b border-line-soft px-3 whitespace-nowrap'
const clock = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})
const stamp = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-4 py-3 text-[12.5px] text-muted-foreground">{children}</p>
  )
}

function statusTone(status: string) {
  if (status === 'ok') return 'ok' as const
  if (status === 'running') return 'neutral' as const
  if (status === 'timeout' || status === 'cancelled') return 'warn' as const
  return 'bad' as const
}

const statusWords: Record<string, string> = {
  ok: 'Ran',
  running: 'Running',
  timeout: 'Timed out',
  cancelled: 'Interrupted',
  failed: 'Failed',
  error: 'Failed',
}

function StatusCell({ status }: { status: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-text-2">
      <Dot tone={statusTone(status)} />
      {statusWords[status] ?? status}
    </span>
  )
}

function VariablesTable({
  variables,
  producedBy,
}: {
  variables: Array<KernelVariable>
  producedBy: Map<string, NotebookCell>
}) {
  if (variables.length === 0)
    return (
      <Empty>
        Nothing in the kernel yet. Run a cell and its results show up here.
      </Empty>
    )
  return (
    <table
      className="w-full border-separate border-spacing-0 text-[12.5px]"
      aria-label="Kernel variables"
    >
      <thead>
        <tr>
          {['Name', 'Type', 'Shape', 'From', 'Preview'].map((name) => (
            <th key={name} scope="col" className={th}>
              {name}
            </th>
          ))}
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

function CellHistory({ history }: { history: Array<HistoryEntry> }) {
  if (history.length === 0)
    return <Empty>No cells have run in this session.</Empty>
  return (
    <table
      className="w-full border-separate border-spacing-0 text-[12.5px]"
      aria-label="Cell history"
    >
      <thead>
        <tr>
          {['Cell', 'Result', 'Took', 'At'].map((name) => (
            <th key={name} scope="col" className={th}>
              {name}
            </th>
          ))}
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
              <StatusCell status={entry.status} />
            </td>
            <td className={cn(td, 'text-text-2')}>
              {entry.ms === undefined ? '–' : `${entry.ms} ms`}
            </td>
            <td className={cn(td, 'text-muted-foreground')}>
              {clock.format(entry.at)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function runDuration(run: NotebookRun) {
  if (!run.finished_at) return '–'
  const ms = Date.parse(run.finished_at) - Date.parse(run.started_at)
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

function runFailure(run: NotebookRun, cells: ReadonlyArray<NotebookCell>) {
  if (run.message) return run.message
  const failed = run.cells.find((cell) => cell.status !== 'ok')
  if (!failed) return ''
  const cell = cells.find((item) => item.id === failed.cell_id)
  return `${cell?.name ?? 'A cell'}: ${failed.error ?? statusWords[failed.status] ?? failed.status}`
}

/** Whole-notebook runs, from Run in background and the schedule, refreshed while one runs. */
export function useNotebookRuns(env: string, path: string) {
  const [runs, setRuns] = React.useState<NotebookRuns | null>(null)
  const [tick, setTick] = React.useState(0)
  const refresh = React.useCallback(() => setTick((value) => value + 1), [])
  React.useEffect(() => {
    let live = true
    getNotebookRuns({ data: { env, path } }).then(
      (result) => live && setRuns(result),
      () => live && setRuns({ items: [], schedules_enabled: false }),
    )
    return () => {
      live = false
    }
  }, [env, path, tick])
  const running = runs?.items.some((run) => run.status === 'running') ?? false
  React.useEffect(() => {
    if (!running) return
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [running, refresh])
  return { runs, refresh, running }
}

function NotebookRunsTable({
  runs,
  cells,
  schedule,
  canRun,
  dirty,
  onRun,
}: {
  runs: NotebookRuns | null
  cells: ReadonlyArray<NotebookCell>
  schedule: NotebookSchedule | null
  canRun: boolean
  dirty: boolean
  onRun: () => void
}) {
  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 text-[12.5px] text-text-2">
        <Button size="sm" variant="outline" disabled={!canRun} onClick={onRun}>
          <PlayIcon className="fill-current" /> Run in background
        </Button>
        <span className="text-muted-foreground">
          {dirty
            ? 'Runs the saved version. Save first to include your edits.'
            : 'Runs the saved notebook in its own kernel; you can close the page.'}
        </span>
        {schedule ? (
          <span className="ml-auto text-muted-foreground">
            {schedule.enabled ? 'Scheduled' : 'Paused'}:{' '}
            {describeCron(schedule.cron)} UTC in {schedule.env}
          </span>
        ) : null}
      </div>
      {runs === null ? (
        <Empty>Loading runs…</Empty>
      ) : runs.items.length === 0 ? (
        <Empty>No background or scheduled runs yet.</Empty>
      ) : (
        <table
          className="w-full border-separate border-spacing-0 text-[12.5px]"
          aria-label="Notebook runs"
        >
          <thead>
            <tr>
              {['Started', 'Trigger', 'Result', 'Took', 'By', 'Detail'].map(
                (name) => (
                  <th key={name} scope="col" className={th}>
                    {name}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {runs.items.map((run) => (
              <tr key={run.id}>
                <td className={cn(td, 'text-text-2')}>
                  {stamp.format(new Date(run.started_at))}
                </td>
                <td className={cn(td, 'text-text-2')}>
                  {run.trigger === 'schedule' ? 'Schedule' : 'Manual'} ·{' '}
                  {run.env}
                </td>
                <td className={td}>
                  <StatusCell status={run.status} />
                </td>
                <td className={cn(td, 'text-text-2')}>{runDuration(run)}</td>
                <td className={cn(td, 'text-text-2')}>{run.actor}</td>
                <td
                  className={cn(
                    td,
                    'max-w-[360px] truncate text-muted-foreground',
                  )}
                  title={runFailure(run, cells)}
                >
                  {run.status === 'running'
                    ? `${run.cells.length} cells done`
                    : runFailure(run, cells) || `${run.cells.length} cells`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function Lineage({
  env,
  reads,
}: {
  env: string
  reads: ReadonlyArray<string>
}) {
  const [found, setFound] = React.useState<
    { items: Array<LineageAsset>; complete: boolean } | 'loading' | 'failed'
  >('loading')
  const key = reads.join('\n')
  React.useEffect(() => {
    if (!key) return
    let live = true
    setFound('loading')
    getNotebookLineage({ data: { env, tables: key.split('\n') } }).then(
      (result) => live && setFound(result),
      () => live && setFound('failed'),
    )
    return () => {
      live = false
    }
  }, [env, key])
  if (reads.length === 0)
    return <Empty>This notebook does not read lakehouse tables yet.</Empty>
  const producer = (table: string) => {
    if (typeof found === 'string') return undefined
    const wanted = table.toLowerCase().split('.').slice(-2).join('.')
    return found.items.find(
      (asset) =>
        asset.relation?.toLowerCase().split('.').slice(-2).join('.') === wanted,
    )
  }
  return (
    <table
      className="w-full border-separate border-spacing-0 text-[12.5px]"
      aria-label="Lineage"
    >
      <thead>
        <tr>
          {['Reads', 'Produced by', 'Kind'].map((name) => (
            <th key={name} scope="col" className={th}>
              {name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {reads.map((table) => {
          const asset = producer(table)
          return (
            <tr key={table}>
              <td className={cn(td, 'font-mono text-foreground')}>
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="size-2 rounded-[2px] bg-gold" />
                  {table}
                </span>
              </td>
              <td className={cn(td, 'text-text-2')}>
                {found === 'loading' ? (
                  <Loader2Icon className="size-3 animate-spin" />
                ) : asset ? (
                  <Link
                    to="/assets/$assetId"
                    params={{ assetId: asset.id }}
                    search={{ env }}
                    className="font-mono text-link hover:underline"
                  >
                    {asset.key.join('/')}
                  </Link>
                ) : found === 'failed' ? (
                  'Assets could not be read'
                ) : (
                  'No Phlo asset writes this table'
                )}
              </td>
              <td className={cn(td, 'text-muted-foreground')}>
                {asset?.compute_kind ?? ''}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

type Tool = 'variables' | 'history' | 'runs' | 'lineage'

export function KernelTools({
  env,
  variables,
  producedBy,
  history,
  reads,
  runs,
}: {
  env: string
  variables: Array<KernelVariable>
  producedBy: Map<string, NotebookCell>
  history: Array<HistoryEntry>
  reads: ReadonlyArray<string>
  runs: React.ComponentProps<typeof NotebookRunsTable>
}) {
  const [tool, setTool] = React.useState<Tool>('variables')
  const [open, setOpen] = React.useState(true)
  const Chevron = open ? ChevronDownIcon : ChevronUpIcon
  const body: Record<Tool, React.ReactNode> = {
    variables: <VariablesTable variables={variables} producedBy={producedBy} />,
    history: <CellHistory history={history} />,
    runs: <NotebookRunsTable {...runs} />,
    lineage: <Lineage env={env} reads={reads} />,
  }
  return (
    <section
      aria-label="Notebook tools"
      className={cn(
        'hidden shrink-0 flex-col border-t border-line lg:flex',
        open && 'h-[212px]',
      )}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <Segmented
          aria-label="Notebook tools"
          value={tool}
          onValueChange={(next) => {
            setTool(next)
            setOpen(true)
          }}
          options={[
            { value: 'variables', label: `Variables · ${variables.length}` },
            { value: 'history', label: 'Cell history' },
            {
              value: 'runs',
              label: runs.runs?.items.length
                ? `Notebook runs · ${runs.runs.items.length}`
                : 'Notebook runs',
            },
            { value: 'lineage', label: 'Lineage' },
          ]}
        />
        <span className="ml-auto" />
        <Button
          variant="ghost"
          size="icon-sm"
          aria-expanded={open}
          aria-label={open ? 'Hide notebook tools' : 'Show notebook tools'}
          onClick={() => setOpen((value) => !value)}
        >
          <Chevron />
        </Button>
      </div>
      {open ? (
        <div className="min-h-0 flex-1 overflow-auto">{body[tool]}</div>
      ) : null}
    </section>
  )
}

function outlineTone(
  id: string,
  states: Record<string, CellState>,
  stale: Map<string, string>,
) {
  if (stale.has(id)) return 'warn' as const
  const status = states[id]?.status
  if (status === 'ok') return 'ok' as const
  if (status === 'error') return 'bad' as const
  return 'neutral' as const
}

export function NotebookOutline({
  cells,
  states,
  stale,
  selected,
  onSelect,
  nessieRef,
  reads,
  packages,
  schedule,
}: {
  cells: Array<NotebookCell>
  states: Record<string, CellState>
  stale: Map<string, string>
  selected: string | null
  onSelect: (id: string) => void
  nessieRef: string
  reads: Array<string>
  packages: ReadonlyArray<string>
  schedule: NotebookSchedule | null
}) {
  const facts: Array<[string, React.ReactNode]> = [
    ['Kernel', 'Python 3'],
    ['Data as of', <Mono key="ref">{nessieRef}</Mono>],
    ['Reads', `${reads.length} ${reads.length === 1 ? 'table' : 'tables'}`],
    ['Cells', String(cells.length)],
    ['Packages', packages.length ? String(packages.length) : 'None'],
    [
      'Schedule',
      schedule
        ? `${schedule.enabled ? '' : 'Paused · '}${describeCron(schedule.cron)}`
        : 'None',
    ],
  ]
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
        {facts.map(([label, value]) => (
          <React.Fragment key={label}>
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="truncate text-right text-text-2">{value}</dd>
          </React.Fragment>
        ))}
      </dl>
    </aside>
  )
}

export function SaveStatus({
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

export function KernelNotice({
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
