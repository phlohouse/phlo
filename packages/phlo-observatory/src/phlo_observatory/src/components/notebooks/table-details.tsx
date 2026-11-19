/** A table or view's details: columns with comments, a data preview, its definition and size. */
import * as React from 'react'
import {
  CopyIcon,
  EyeIcon,
  Loader2Icon,
  PlayIcon,
  TableIcon,
} from 'lucide-react'
import type { CatalogDetails, CatalogSample } from '@/lib/data/api/catalog'
import { getCatalogDetails, getCatalogSample } from '@/lib/data/api/catalog'
import { ResultsGrid } from '@/components/query/result-views'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Segmented } from '@/components/ui/toggle-group'
import { Mono } from '@/components/phlo/status'
import { cn } from '@/lib/utils'

export type TableRef = { env: string; schema: string; table: string }

type Loaded<T> =
  | { state: 'loading' }
  | { state: 'ok'; data: T }
  | { state: 'unavailable'; message: string }

function useLoad<T>(
  load: () => Promise<
    { state: 'ok'; data: T } | { state: 'unavailable'; message: string }
  >,
  key: string,
  enabled: boolean,
): Loaded<T> {
  const [value, setValue] = React.useState<Loaded<T>>({ state: 'loading' })
  const loader = React.useRef(load)
  loader.current = load
  React.useEffect(() => {
    if (!enabled) return
    let live = true
    setValue({ state: 'loading' })
    loader.current().then(
      (result) => live && setValue(result),
      (error: unknown) =>
        live &&
        setValue({
          state: 'unavailable',
          message: error instanceof Error ? error.message : 'Could not load.',
        }),
    )
    return () => {
      live = false
    }
  }, [key, enabled])
  return value
}

const count = new Intl.NumberFormat()
const when = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})

function Pending<T>({
  value,
  children,
}: {
  value: Loaded<T>
  children: (data: T) => React.ReactNode
}) {
  if (value.state === 'loading')
    return (
      <p className="flex items-center gap-2 px-4 py-6 text-[13px] text-muted-foreground">
        <Loader2Icon className="size-3.5 animate-spin" /> Loading
      </p>
    )
  if (value.state === 'unavailable')
    return (
      <p role="alert" className="px-4 py-6 text-[13px] text-bad-text">
        {value.message}
      </p>
    )
  return <>{children(value.data)}</>
}

function ColumnsTable({ details }: { details: CatalogDetails }) {
  const th =
    'sticky top-0 h-8 border-b border-line bg-card px-3 text-left text-xs font-normal text-muted-foreground'
  const td = 'border-b border-line-soft px-3 py-1.5 align-top'
  return (
    <table className="w-full border-separate border-spacing-0 text-[12.5px]">
      <thead>
        <tr>
          <th scope="col" className={th}>
            Column
          </th>
          <th scope="col" className={th}>
            Type
          </th>
          <th scope="col" className={th}>
            Comment
          </th>
        </tr>
      </thead>
      <tbody>
        {details.columns.map((column) => (
          <tr key={column.name}>
            <td className={cn(td, 'font-mono text-foreground')}>
              {column.name}
            </td>
            <td className={cn(td, 'font-mono text-[12px] text-text-2')}>
              {column.data_type ?? '–'}
            </td>
            <td className={cn(td, 'text-text-2')}>{column.comment ?? ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Facts({ details }: { details: CatalogDetails }) {
  const facts: Array<[string, React.ReactNode]> = [
    ['Kind', details.kind === 'view' ? 'View' : 'Table'],
    ['Columns', String(details.columns.length)],
  ]
  if (details.row_count !== null)
    facts.push(['Rows', count.format(details.row_count)])
  if (details.committed_at)
    facts.push(['Last change', when.format(new Date(details.committed_at))])
  if (details.snapshot_id)
    facts.push(['Snapshot', <Mono key="s">{details.snapshot_id}</Mono>])
  facts.push(['Data as of', <Mono key="r">{details.nessie_ref}</Mono>])
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 px-4 py-3 text-[12.5px] sm:grid-cols-3">
      {facts.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="truncate text-text-2">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Preview({ sample }: { sample: CatalogSample }) {
  return (
    <div className="flex max-h-[360px] flex-col">
      <ResultsGrid
        result={{
          columns: sample.columns.map((column) => ({
            name: column.name,
            type: column.type ?? null,
          })),
          rows: sample.rows,
          has_more: sample.has_more,
        }}
      />
      <p className="border-t border-line-soft px-4 py-2 text-xs text-muted-foreground">
        The first {sample.rows.length} rows
        {sample.has_more ? '. There are more.' : '.'}
      </p>
    </div>
  )
}

export function TableDetailsDialog({
  target,
  qualified,
  canQuery,
  onClose,
  onQuery,
}: {
  target: TableRef | null
  /** catalog.schema.table, quoted where needed */
  qualified: string
  /** Whether a cell in this notebook can read it (same environment) */
  canQuery: boolean
  onClose: () => void
  onQuery: () => void
}) {
  const [tab, setTab] = React.useState<'columns' | 'preview' | 'ddl'>('columns')
  const key = target ? `${target.env}.${target.schema}.${target.table}` : ''
  const details = useLoad(
    () => getCatalogDetails({ data: target as TableRef }),
    key,
    target !== null,
  )
  const sample = useLoad(
    () => getCatalogSample({ data: target as TableRef }),
    key,
    target !== null && tab === 'preview',
  )
  React.useEffect(() => setTab('columns'), [key])
  const Icon =
    details.state === 'ok' && details.data.kind === 'view' ? EyeIcon : TableIcon
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[760px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Icon className="size-4 text-faint" />
            <span className="truncate font-mono text-[14px]">
              {target ? `${target.schema}.${target.table}` : ''}
            </span>
          </DialogTitle>
          <DialogDescription>
            {details.state === 'ok' && details.data.comment
              ? details.data.comment
              : `In ${target?.env ?? ''}, read-only.`}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-0 p-0">
          <Pending value={details}>
            {(data) => <Facts details={data} />}
          </Pending>
          <div className="flex flex-wrap items-center gap-2 border-y border-line-soft px-4 py-2">
            <Segmented
              aria-label="Table details"
              value={tab}
              onValueChange={setTab}
              options={[
                { value: 'columns', label: 'Columns' },
                { value: 'preview', label: 'Data preview' },
                { value: 'ddl', label: 'Definition' },
              ]}
            />
            <span className="ml-auto" />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void navigator.clipboard.writeText(qualified)}
            >
              <CopyIcon /> Copy name
            </Button>
            <Button
              size="sm"
              disabled={!canQuery}
              title={
                canQuery
                  ? undefined
                  : 'Cells read this notebook’s environment only'
              }
              onClick={onQuery}
            >
              <PlayIcon className="fill-current" /> Query in a cell
            </Button>
          </div>
          <div className="max-h-[420px] min-h-[160px] overflow-auto">
            {tab === 'columns' ? (
              <Pending value={details}>
                {(data) => <ColumnsTable details={data} />}
              </Pending>
            ) : tab === 'preview' ? (
              <Pending value={sample}>
                {(data) => <Preview sample={data} />}
              </Pending>
            ) : (
              <Pending value={details}>
                {(data) => (
                  <pre className="m-0 px-4 py-3 font-mono text-[12px] leading-5 whitespace-pre-wrap text-text-2">
                    {data.ddl ??
                      'Trino did not return a definition for this object.'}
                  </pre>
                )}
              </Pending>
            )}
          </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
