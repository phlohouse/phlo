/** One notebook cell: header, editor, what it reads, its outputs and its result table. */
import * as React from 'react'
import {
  ArrowDownIcon,
  ArrowUpIcon,
  EllipsisIcon,
  Loader2Icon,
  PencilIcon,
  PlayIcon,
  Trash2Icon,
} from 'lucide-react'
import type { QueryResult } from '@/lib/data/api/query'
import type { CellRun, CellTable } from '@/lib/data/api/notebooks'
import type { CellState, NotebookCell } from '@/lib/notebook'
import { RESULT_NAME, cellLabel, sqlReads } from '@/lib/notebook'
import { CellEditor } from '@/components/notebooks/cell-editor'
import { Markdown } from '@/components/notebooks/markdown'
import {
  ResultsChart,
  ResultsGrid,
  ResultsPivot,
} from '@/components/query/result-views'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/menu'
import { Segmented } from '@/components/ui/toggle-group'
import { Dot } from '@/components/phlo/status'
import { cn } from '@/lib/utils'

const clock = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
})

function asResult(table: CellTable): QueryResult {
  return {
    columns: table.columns,
    rows: table.rows,
    has_more: table.row_count > table.rows.length,
  }
}

function duration(ms: number) {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

function RunStatus({ state }: { state?: CellState }) {
  if (!state) return null
  if (state.status === 'running')
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2Icon className="size-3 animate-spin" /> Running
      </span>
    )
  const tone =
    state.status === 'ok' ? 'ok' : state.status === 'timeout' ? 'warn' : 'bad'
  const verb =
    state.status === 'ok'
      ? 'Ran'
      : state.status === 'timeout'
        ? 'Timed out'
        : 'Failed'
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap text-muted-foreground">
      <Dot tone={tone} />
      {verb} {clock.format(state.at)}
      {state.run ? ` · ${duration(state.run.duration_ms)}` : ''}
    </span>
  )
}

function Outputs({ run }: { run: CellRun }) {
  const shown = run.outputs.filter(
    (output) =>
      output.type !== 'stream' ||
      (output.text && output.text.trim().length > 0),
  )
  if (shown.length === 0 && !run.truncated) return null
  return (
    <div className="flex flex-col gap-1.5 border-t border-line-soft px-3.5 py-2.5">
      {shown.map((output, index) => {
        if (output.type === 'image' && output.png)
          return (
            <img
              key={index}
              src={`data:image/png;base64,${output.png}`}
              alt="Figure from this cell"
              className="max-w-full self-start rounded-md bg-white"
            />
          )
        if (output.type === 'error')
          return (
            <div
              key={index}
              role="alert"
              className="rounded-md border border-bad-line bg-bad-wash px-3 py-2"
            >
              <div className="text-[12.5px] font-medium text-bad-text">
                {output.ename ?? 'Error'}
              </div>
              <pre className="m-0 mt-1 max-h-72 overflow-auto font-mono text-[12px] leading-5 whitespace-pre-wrap text-text-2">
                {output.text}
              </pre>
            </div>
          )
        return (
          <pre
            key={index}
            className={cn(
              'm-0 max-h-72 overflow-auto font-mono text-[12.5px] leading-5 whitespace-pre-wrap',
              output.name === 'stderr' ? 'text-warn-ink' : 'text-text-2',
            )}
          >
            {output.text}
          </pre>
        )
      })}
      {run.truncated ? (
        <p className="text-xs text-muted-foreground">
          Output was cut at 200,000 characters.
        </p>
      ) : null}
    </div>
  )
}

function TableOutput({
  table,
  name,
  capped,
}: {
  table: CellTable
  name: string | null
  capped?: boolean
}) {
  const [view, setView] = React.useState<'results' | 'chart' | 'pivot'>(
    'results',
  )
  const result = asResult(table)
  const total = table.row_count
  return (
    <div className="border-t border-line-soft">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <Segmented
          aria-label="Result view"
          value={view}
          onValueChange={setView}
          options={[
            { value: 'results', label: `Results · ${total}` },
            { value: 'chart', label: 'Chart' },
            { value: 'pivot', label: 'Pivot' },
          ]}
        />
      </div>
      {view === 'results' ? (
        <div className="flex max-h-[320px] flex-col border-t border-line-soft">
          <ResultsGrid result={result} />
        </div>
      ) : view === 'chart' ? (
        <div className="flex h-[420px] flex-col border-t border-line-soft">
          <ResultsChart result={result} />
        </div>
      ) : (
        <div className="flex max-h-[360px] flex-col overflow-auto border-t border-line-soft">
          <ResultsPivot result={result} />
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-soft px-3.5 py-2 text-xs text-muted-foreground">
        <span>
          Showing {table.rows.length} of {total} rows
        </span>
        {name ? (
          <span>
            In Python as <code className="font-mono text-link">{name}</code>
            {capped ? ', the first 100 rows the lakehouse returned' : ''}
          </span>
        ) : null}
      </div>
    </div>
  )
}

export type CellActions = {
  change: (patch: Partial<NotebookCell>) => void
  run: () => void
  move: (by: -1 | 1) => void
  remove: () => void
  select: () => void
}

type CellProps = {
  cell: NotebookCell
  index: number
  count: number
  state?: CellState
  /** The upstream cell whose newer result makes this one stale */
  staleFrom?: NotebookCell
  selected: boolean
  canRun: boolean
  actions: CellActions
}

function CellMenu({
  cell,
  index,
  count,
  actions,
  onEdit,
}: Pick<CellProps, 'cell' | 'index' | 'count' | 'actions'> & {
  onEdit?: () => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={`More for ${cell.name ?? `cell ${index + 1}`}`}
        className="inline-flex size-7 items-center justify-center rounded-md text-text-3 hover:bg-soft hover:text-foreground"
      >
        <EllipsisIcon className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {onEdit ? (
          <DropdownMenuItem onClick={onEdit}>
            <PencilIcon /> Edit text
          </DropdownMenuItem>
        ) : null}
        {cell.kind === 'sql' ? (
          <DropdownMenuItem
            onClick={() =>
              actions.change({
                engine: cell.engine === 'duckdb' ? 'trino' : 'duckdb',
              })
            }
          >
            Run on {cell.engine === 'duckdb' ? 'Trino' : 'DuckDB'}
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem
          disabled={index === 0}
          onClick={() => actions.move(-1)}
        >
          <ArrowUpIcon /> Move up
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={index === count - 1}
          onClick={() => actions.move(1)}
        >
          <ArrowDownIcon /> Move down
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={actions.remove}>
          <Trash2Icon /> Delete cell
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function TextCell(props: CellProps) {
  const { cell, index, selected, actions } = props
  const [editing, setEditing] = React.useState(cell.source.trim() === '')
  const menu = <CellMenu {...props} onEdit={() => setEditing(true)} />
  return (
    <section
      id={`cell-${cell.id}`}
      aria-label={`Text cell ${index + 1}`}
      onFocusCapture={actions.select}
      className={cn(
        'group relative scroll-mt-4 rounded-[10px] border border-transparent',
        editing && 'border-border bg-card',
        selected && !editing && 'border-line',
      )}
    >
      {editing ? (
        <>
          <div className="flex items-center gap-2 border-b border-line-soft px-3 py-1.5">
            <Badge variant="outline" size="sm">
              Text
            </Badge>
            <span className="ml-auto" />
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Done
            </Button>
            {menu}
          </div>
          <CellEditor
            value={cell.source}
            onChange={(source) => actions.change({ source })}
            onRun={() => setEditing(false)}
            language="text"
            label={`Text for cell ${index + 1}`}
          />
        </>
      ) : (
        <div className="flex items-start gap-2 px-0.5 py-1">
          <button
            type="button"
            aria-label={`Edit text cell ${index + 1}`}
            onDoubleClick={() => setEditing(true)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') setEditing(true)
            }}
            className="min-w-0 flex-1 cursor-text rounded-md text-left outline-none focus-visible:outline-2 focus-visible:outline-ring"
          >
            {cell.source.trim() ? (
              <Markdown source={cell.source} />
            ) : (
              <span className="text-sm text-faint">Empty text cell</span>
            )}
          </button>
          <div className="opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
            {menu}
          </div>
        </div>
      )}
    </section>
  )
}

function NameField({
  cell,
  index,
  onChange,
}: {
  cell: NotebookCell
  index: number
  onChange: (name: string | null) => void
}) {
  const [draft, setDraft] = React.useState(cell.name ?? '')
  React.useEffect(() => setDraft(cell.name ?? ''), [cell.name])
  const valid = draft === '' || RESULT_NAME.test(draft)
  return (
    <input
      aria-label={`Result name for cell ${index + 1}`}
      aria-invalid={!valid}
      value={draft}
      placeholder={cell.kind === 'sql' ? 'name' : 'result name'}
      maxLength={64}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        // SQL results always need a name: the kernel binds rows under it.
        if (!valid || (cell.kind === 'sql' && draft === ''))
          return setDraft(cell.name ?? '')
        onChange(draft || null)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
      }}
      className={cn(
        'h-6 max-w-[16rem] min-w-[4ch] rounded-md bg-transparent px-1 [field-sizing:content] font-mono text-[13px] font-medium text-foreground outline-none placeholder:font-normal placeholder:text-faint hover:bg-soft focus:bg-soft',
        !valid && 'text-bad-text',
      )}
    />
  )
}

function ReadsStrip({ reads }: { reads: Array<string> }) {
  if (!reads.length) return null
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line-soft bg-raised px-3.5 py-1.5 text-xs text-muted-foreground">
      <span>Reads</span>
      {reads.map((table) => (
        <span
          key={table}
          className="inline-flex items-center gap-1.5 font-mono text-[12px] text-text-2"
        >
          <span aria-hidden className="size-2 rounded-[2px] bg-gold" />
          {table}
        </span>
      ))}
    </div>
  )
}

function StaleNote({
  from,
  disabled,
  onRun,
}: {
  from: NotebookCell
  disabled: boolean
  onRun: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-warn-soft bg-warn-wash px-3.5 py-2 text-[12.5px] text-warn-ink">
      <span className="min-w-0 flex-1">
        <code className="font-mono">{from.name}</code> ran again after this
        result was made. The result below may be out of date.
      </span>
      <Button size="sm" variant="outline" disabled={disabled} onClick={onRun}>
        Run this cell
      </Button>
    </div>
  )
}

function CellResults({
  cell,
  state,
}: {
  cell: NotebookCell
  state?: CellState
}) {
  return (
    <>
      {state?.message ? (
        <div
          role="alert"
          className="border-t border-bad-line bg-bad-wash px-3.5 py-2 text-[12.5px] text-bad-text"
        >
          {state.message}
        </div>
      ) : null}
      {state?.run ? <Outputs run={state.run} /> : null}
      {state?.run?.table ? (
        <TableOutput
          table={state.run.table}
          name={cell.name}
          capped={state.capped}
        />
      ) : null}
    </>
  )
}

function RunButton({
  label,
  canRun,
  running,
  onRun,
}: {
  label: string
  canRun: boolean
  running: boolean
  onRun: () => void
}) {
  return (
    <button
      type="button"
      aria-label={`Run ${label}`}
      title={canRun ? 'Run cell (Shift+Enter)' : 'Kernels are off'}
      disabled={!canRun || running}
      onClick={onRun}
      className="inline-flex size-7 items-center justify-center rounded-md text-primary hover:bg-primary-soft disabled:cursor-not-allowed disabled:text-faint disabled:hover:bg-transparent"
    >
      {running ? (
        <Loader2Icon className="size-3.5 animate-spin" />
      ) : (
        <PlayIcon className="size-3.5 fill-current" />
      )}
    </button>
  )
}

function CodeCell(props: CellProps) {
  const { cell, index, state, staleFrom, selected, canRun, actions } = props
  const running = state?.status === 'running'
  const label = cell.name ?? `cell ${index + 1}`
  const reads =
    cell.kind === 'sql' && cell.engine !== 'duckdb' ? sqlReads(cell.source) : []
  return (
    <section
      id={`cell-${cell.id}`}
      aria-label={`${cellLabel(cell)} cell ${label}`}
      onFocusCapture={actions.select}
      className={cn(
        'scroll-mt-4 overflow-hidden rounded-[10px] border bg-card',
        selected
          ? 'border-primary shadow-[0_0_0_3px_var(--primary-soft)]'
          : 'border-border',
      )}
    >
      <header className="flex min-h-10 flex-wrap items-center gap-x-2 gap-y-1 border-b border-line-soft px-2 py-1.5">
        <RunButton
          label={label}
          canRun={canRun}
          running={running}
          onRun={actions.run}
        />
        <span className="font-mono text-xs text-faint">
          [{state?.run?.execution_count ?? ' '}]
        </span>
        <NameField
          cell={cell}
          index={index}
          onChange={(name) => actions.change({ name })}
        />
        <Badge variant="outline" size="sm">
          {cellLabel(cell)}
        </Badge>
        {staleFrom ? (
          <Badge variant="warn" size="sm">
            Stale
          </Badge>
        ) : null}
        <span className="ml-auto" />
        <RunStatus state={state} />
        <CellMenu {...props} />
      </header>
      <CellEditor
        value={cell.source}
        onChange={(source) => actions.change({ source })}
        onRun={canRun ? actions.run : undefined}
        language={cell.kind === 'sql' ? 'sql' : 'python'}
        label={`${cellLabel(cell)} for ${label}`}
      />
      <ReadsStrip reads={reads} />
      {staleFrom ? (
        <StaleNote
          from={staleFrom}
          disabled={!canRun || running}
          onRun={actions.run}
        />
      ) : null}
      <CellResults cell={cell} state={state} />
    </section>
  )
}

export function NotebookCellView(props: CellProps) {
  return props.cell.kind === 'markdown' ? (
    <TextCell {...props} />
  ) : (
    <CodeCell {...props} />
  )
}
