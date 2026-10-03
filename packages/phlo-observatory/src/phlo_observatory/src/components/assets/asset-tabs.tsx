/** Renders asset tabs from observed metadata, query results, and check evidence. */
import { Link } from '@tanstack/react-router'
import { CodeIcon, DatabaseIcon, PlusIcon, XIcon } from 'lucide-react'
import * as React from 'react'
import type { ReactNode } from 'react'
import type {
  ApiAssetDetail,
  AssetChecks,
  AssetPreview,
  AssetSchemaHistory,
  AssetSnapshots,
  PreviewFilter,
} from '@/lib/data/api/assets'
import type { Env, LineageColumn } from '@/lib/data/types'
import {
  getAssetPreview,
  previewFilterSchema,
  rollbackAssetSnapshot,
} from '@/lib/data/api/assets'
import { LineageGraph } from '@/components/assets/lineage-graph'
import { Stat } from '@/components/phlo/kpi'
import { Eyebrow, KeyValues } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Dot, Mono } from '@/components/phlo/status'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { CheckLine } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

const pad = 'px-4 py-5 lg:px-7'

export function UnavailableTab({
  message,
  action,
}: {
  message: string
  action?: ReactNode
}) {
  return (
    <div className={pad}>
      <EmptyState title="This asset view is unavailable" action={action}>
        {message}
      </EmptyState>
    </div>
  )
}

export function OverviewTab({
  asset,
  env,
  jobs,
}: {
  asset: ApiAssetDetail
  env: Env
  jobs: Array<{ id: string }>
}) {
  return (
    <div className="grid min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <section className={cn(pad, 'flex min-w-0 flex-col gap-[22px]')}>
        <div className="flex flex-col gap-2.5">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1.5">
            <Eyebrow>Rows per run</Eyebrow>
            <div className="ml-auto flex items-center gap-3.5 text-[13px] text-muted-foreground">
              <span>Loaded</span>
              <span>Skipped</span>
              <span>Failed</span>
            </div>
          </div>
          <div className="flex h-24 items-center justify-center rounded-lg border border-dashed border-line text-[13px] text-muted-foreground">
            Row counts per materialization are not available.
          </div>
        </div>
        <div>
          <div className="flex items-baseline pb-1.5">
            <Eyebrow>Schema · {asset.columns.length} declared columns</Eyebrow>
            <span className="ml-auto text-[13px] text-muted-foreground">
              Contract: not observed
            </span>
          </div>
          <div
            role="table"
            aria-label="Observed asset schema"
            className="overflow-x-auto"
          >
            <div className="min-w-[520px]">
              <div
                role="row"
                className="grid h-8 grid-cols-[150px_100px_72px_minmax(0,1fr)] items-center gap-x-3.5 border-b border-line-soft text-xs text-muted-foreground"
              >
                <span role="columnheader">Column</span>
                <span role="columnheader">Type</span>
                <span role="columnheader">Nullable</span>
                <span role="columnheader">Notes</span>
              </div>
              {asset.columns.map((column) => (
                <div
                  role="row"
                  key={column.name}
                  className="grid h-10 grid-cols-[150px_100px_72px_minmax(0,1fr)] items-center gap-x-3.5 border-b border-line-soft text-[13.5px] last:border-0"
                >
                  <Mono>{column.name}</Mono>
                  <Mono className="text-muted-foreground">
                    {column.type === 'None'
                      ? 'Unknown'
                      : (column.type ?? 'Unknown')}
                  </Mono>
                  <span role="cell" className="text-muted-foreground">
                    Unknown
                  </span>
                  <span role="cell" className="truncate text-muted-foreground">
                    {column.description ?? 'Not supplied'}
                  </span>
                </div>
              ))}
              {!asset.columns.length ? (
                <p className="px-4 py-3 text-sm text-muted-foreground">
                  No column schema has been observed.
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </section>
      <aside className="flex flex-col gap-[22px] border-t border-line px-4 py-5 lg:border-t-0 lg:border-l lg:px-6">
        <KeyValues
          keyWidth={112}
          className="text-[13.5px] [&_dd]:break-all"
          items={[
            ['Owner', 'Not observed'],
            ['Source', asset.compute_kind ?? 'Not observed'],
            [
              'Job',
              jobs.length ? (
                <span className="flex min-w-0 flex-1 flex-col gap-1">
                  {jobs.map((job) => (
                    <Link
                      key={job.id}
                      to="/pipelines/$jobName"
                      params={{ jobName: job.id }}
                      search={{ env }}
                      title={job.id}
                      className="truncate font-mono text-[12.5px]"
                    >
                      {job.id}
                    </Link>
                  ))}
                </span>
              ) : (
                'None available'
              ),
            ],
            ['Freshness SLA', 'Not observed'],
            ['Rows', 'Not observed'],
            ['Size', 'Not observed'],
            ['Sort order', 'Not observed'],
            ['Relation', asset.relation ?? 'Not observed'],
            [
              'Last materialized',
              asset.last_materialization_at ?? 'Not observed',
            ],
          ]}
        />
        <div className="flex flex-col gap-2">
          <Eyebrow>Audits</Eyebrow>
          <span className="text-[13px] text-muted-foreground">
            Open the Audits tab for current check evidence.
          </span>
        </div>
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline">
            <Eyebrow>Downstream</Eyebrow>
            <Link
              to="/assets/$assetId"
              params={{ assetId: asset.id }}
              search={{ env, tab: 'lineage' }}
              className="ml-auto text-[13px]"
            >
              Blast radius
            </Link>
          </div>
          <span className="text-[13px] text-muted-foreground">
            Downstream inventory is not supplied. {asset.dependencies.length}{' '}
            upstream dependencies are declared.
          </span>
        </div>
      </aside>
    </div>
  )
}

export function DataTab({
  asset,
  data: initialData,
}: {
  asset: ApiAssetDetail
  data: AssetPreview
}) {
  const [data, setData] = React.useState(initialData)
  const [filters, setFilters] = React.useState<Array<PreviewFilter>>([])
  const [editing, setEditing] = React.useState(false)
  const [column, setColumn] = React.useState(initialData.columns[0]?.name ?? '')
  const [operator, setOperator] =
    React.useState<PreviewFilter['operator']>('eq')
  const [valueType, setValueType] = React.useState('text')
  const [value, setValue] = React.useState('')
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const requestId = React.useRef(0)
  React.useEffect(
    () => () => {
      requestId.current += 1
    },
    [],
  )
  async function apply(next: Array<PreviewFilter>) {
    const sequence = ++requestId.current
    setPending(true)
    setError(null)
    try {
      const result = await getAssetPreview({
        data: { env: initialData.env, id: asset.id, filters: next },
      })
      if (sequence !== requestId.current) return
      setData(result)
      setFilters(next)
      setEditing(false)
    } catch (cause) {
      if (sequence === requestId.current)
        setError(cause instanceof Error ? cause.message : 'Preview failed.')
    } finally {
      if (sequence === requestId.current) setPending(false)
    }
  }
  function addFilter(event: React.FormEvent) {
    event.preventDefault()
    const parsed = previewFilterSchema.safeParse(
      operator === 'is_null' || operator === 'is_not_null'
        ? { column, operator }
        : {
            column,
            operator,
            value:
              valueType === 'number'
                ? value.trim()
                  ? Number(value)
                  : NaN
                : valueType === 'boolean'
                  ? value === 'true'
                  : value,
          },
    )
    if (!parsed.success) {
      setError('Enter a valid filter value.')
      return
    }
    void apply([...filters, parsed.data])
  }
  const operators = [
    { value: 'eq', label: '=' },
    { value: 'ne', label: '≠' },
    { value: 'lt', label: '<' },
    { value: 'lte', label: '≤' },
    { value: 'gt', label: '>' },
    { value: 'gte', label: '≥' },
    { value: 'is_null', label: 'is null' },
    { value: 'is_not_null', label: 'is not null' },
  ] satisfies Array<{ value: PreviewFilter['operator']; label: string }>
  return (
    <div className="flex min-h-0 flex-col" aria-busy={pending}>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3 text-[13px] text-muted-foreground lg:px-7">
        {filters.map((filter, index) => (
          <span
            key={index}
            className="flex h-7 items-center gap-1.5 rounded-full border border-border bg-card pr-1 pl-3 text-text-2"
          >
            <span className="text-muted-foreground">{filter.column}</span>
            {operators.find((item) => item.value === filter.operator)?.label}
            {'value' in filter ? <Mono>{String(filter.value)}</Mono> : null}
            <button
              type="button"
              disabled={pending}
              aria-label={`Remove filter ${index + 1} on ${filter.column}`}
              onClick={() => void apply(filters.filter((_, i) => i !== index))}
              className="inline-flex size-5 items-center justify-center rounded-full hover:bg-soft"
            >
              <XIcon className="size-3" />
            </button>
          </span>
        ))}
        <Button
          variant="outline"
          className="h-7 rounded-full border-dashed"
          disabled={pending || !data.columns.length || filters.length >= 20}
          onClick={() => setEditing(!editing)}
        >
          <PlusIcon /> Add filter
        </Button>
        <DatabaseIcon className="size-4" /> Preview on{' '}
        <Mono>{data.nessie_ref}</Mono>
        <Link
          to="/query"
          search={() => ({ env: data.env, sql: data.sql })}
          className={cn(
            buttonVariants({ variant: 'outline' }),
            'h-[30px] sm:ml-auto',
          )}
        >
          <CodeIcon /> Open in Query
        </Link>
      </div>
      {editing ? (
        <form
          onSubmit={addFilter}
          className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3 lg:px-7"
        >
          <Select
            aria-label="Filter column"
            className="w-full sm:w-44"
            value={column}
            onValueChange={setColumn}
            options={data.columns.map((item) => ({
              value: item.name,
              label: item.name,
            }))}
          />
          <Select
            aria-label="Filter operator"
            className="w-full sm:w-32"
            value={operator}
            onValueChange={setOperator}
            options={operators}
          />
          {operator !== 'is_null' && operator !== 'is_not_null' ? (
            <>
              <Select
                aria-label="Filter value type"
                className="w-full sm:w-32"
                value={valueType}
                onValueChange={(type) => {
                  setValueType(type)
                  setValue(type === 'boolean' ? 'true' : '')
                }}
                options={[
                  { value: 'text', label: 'Text' },
                  { value: 'number', label: 'Number' },
                  { value: 'boolean', label: 'Boolean' },
                ]}
              />
              {valueType === 'boolean' ? (
                <Select
                  aria-label="Filter value"
                  className="w-full sm:w-32"
                  value={value}
                  onValueChange={setValue}
                  options={[
                    { value: 'true', label: 'true' },
                    { value: 'false', label: 'false' },
                  ]}
                />
              ) : (
                <Input
                  aria-label="Filter value"
                  type={valueType === 'number' ? 'number' : 'text'}
                  step="any"
                  className="w-full sm:w-44"
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                />
              )}
            </>
          ) : null}
          <Button type="submit" disabled={pending}>
            {pending ? 'Applying…' : 'Apply filter'}
          </Button>
        </form>
      ) : null}
      {error ? (
        <p role="alert" className="px-4 text-sm text-bad-text lg:px-7">
          {error} The previous preview is unchanged.
        </p>
      ) : null}
      <div className="overflow-x-auto">
        <table
          className="w-full min-w-max border-collapse font-mono text-[12.5px]"
          aria-label={`Sample rows from ${asset.id}`}
        >
          <thead>
            <tr className="bg-raised">
              <th className="h-[34px] border-b border-line px-3 text-right font-sans font-normal text-muted-foreground">
                #
              </th>
              {data.columns.map((previewColumn) => (
                <th
                  key={previewColumn.name}
                  className="border-b border-line px-3 text-left font-normal"
                >
                  {previewColumn.name}{' '}
                  <span className="text-[10.5px] text-faint">
                    {previewColumn.type ?? 'unknown'}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, index) => (
              <tr key={index}>
                {[
                  index + 1,
                  ...data.columns.map(
                    (previewColumn) => row[previewColumn.name],
                  ),
                ].map((cellValue, cell) => (
                  <td
                    key={cell}
                    className="h-8 border-r border-b border-line-soft px-3 whitespace-nowrap last:border-r-0"
                  >
                    {cellValue === null ? (
                      <span className="text-faint">NULL</span>
                    ) : typeof cellValue === 'object' ? (
                      JSON.stringify(cellValue)
                    ) : (
                      String(cellValue ?? '')
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!data.rows.length ? (
        <div className={pad}>
          <EmptyState title="No rows returned" />
        </div>
      ) : null}
      <div className="flex gap-4 border-t border-line px-4 py-3 text-[13px] text-muted-foreground lg:px-7">
        <span>{data.rows.length} rows shown</span>
        {data.has_more ? <span>More rows available</span> : null}
      </div>
    </div>
  )
}

export function SchemaTab({ data }: { data: AssetSchemaHistory }) {
  return (
    <div className="grid min-h-0 grid-cols-1 lg:grid-cols-[380px_minmax(0,1fr)]">
      <section className="flex flex-col gap-1 border-b border-line px-3 py-4 lg:border-r lg:border-b-0 lg:px-4 lg:py-[18px]">
        <Eyebrow className="px-2.5 pb-2">{data.items.length} versions</Eyebrow>
        {data.items.map((version) => (
          <div
            key={version.schema_id}
            className={cn(
              'rounded-[10px] border p-3',
              version.schema_id === data.current_schema_id
                ? 'border-branch-line bg-branch-soft'
                : 'border-transparent',
            )}
          >
            <Mono>Schema {version.schema_id}</Mono>
            {version.schema_id === data.current_schema_id ? (
              <Badge variant="neutral" className="ml-2">
                Current
              </Badge>
            ) : null}
            <div className="mt-1 text-xs text-muted-foreground">
              {version.fields.length} fields
            </div>
          </div>
        ))}
      </section>
      <section className={cn(pad, 'min-w-0')}>
        <Eyebrow className="mb-3">
          Schema history · ref {data.nessie_ref}
        </Eyebrow>
        {data.items.map((version) => (
          <div key={version.schema_id} className="mb-6">
            <h3 className="text-sm font-medium">Schema {version.schema_id}</h3>
            <div className="overflow-x-auto rounded-[10px] border border-border-card">
              <div className="min-w-[480px]">
                {version.fields.map((field) => (
                  <div
                    key={field.name}
                    className="grid h-9 grid-cols-[1fr_1fr_100px] items-center border-b border-line-soft px-4 font-mono text-[12.5px] last:border-0"
                  >
                    <span>{field.name}</span>
                    <span>{field.type}</span>
                    <span>{field.required ? 'required' : 'optional'}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
        {!data.items.length ? <EmptyState title="No schema history" /> : null}
      </section>
    </div>
  )
}

export function LineageTab({
  asset,
  env,
}: {
  asset: ApiAssetDetail
  env: Env
}) {
  const upstream = asset.dependencies.map((key) => key.join('/'))
  const downstream = asset.downstream ?? []
  const reports = [
    ...new Set([
      ...(asset.reports ?? []),
      ...downstream.flatMap((item) => item.reports ?? []),
    ]),
  ]
  const columns: Array<LineageColumn> = [
    {
      heading: 'Declared upstream',
      nodes: upstream.map((id) => ({
        id,
        name: id,
        sub: 'Freshness not observed',
        tone: 'source',
        href: `/assets/${encodeURIComponent(id)}`,
      })),
    },
    {
      heading: 'This asset',
      nodes: [
        {
          id: asset.id,
          name: asset.id,
          sub: asset.relation ?? 'Relation not observed',
          tone: 'self',
        },
      ],
    },
  ]
  const seen = new Set([asset.id, ...upstream])
  let remaining = downstream.filter((item) => !seen.has(item.id))
  while (remaining.length) {
    const layer = remaining.filter((item) =>
      item.dependencies.some((key) => seen.has(key.join('/'))),
    )
    if (!layer.length) break
    columns.push({
      heading: `Downstream · ${columns.length - 1}`,
      nodes: layer.map((item) => ({
        id: item.id,
        name: item.id,
        sub: item.relation ?? 'Relation not observed',
        tone: 'job',
        href: `/assets/${encodeURIComponent(item.id)}`,
      })),
    })
    layer.forEach((item) => seen.add(item.id))
    remaining = remaining.filter((item) => !seen.has(item.id))
  }
  const edges: Array<[string, string]> = [
    ...upstream.map((id): [string, string] => [id, asset.id]),
    ...downstream.flatMap((item) =>
      item.dependencies.map((key): [string, string] => [
        key.join('/'),
        item.id,
      ]),
    ),
  ]
  return (
    <div className={cn(pad, 'flex min-h-0 flex-col gap-[18px]')}>
      <div className="flex flex-wrap items-baseline gap-x-2.5">
        <div className="text-[15px] font-medium">Upstream and downstream</div>
        <div className="text-[13px] text-muted-foreground">
          Declared asset dependencies · freshness not observed
        </div>
      </div>
      <LineageGraph
        env={env}
        columns={columns}
        edges={edges}
        label={`${upstream.length} declared upstream assets feed ${asset.id}; ${downstream.length} downstream assets depend on it in ${env}.`}
      />
      <div className="grid grid-cols-1 gap-6 border-t border-line pt-4 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Eyebrow>Column-level use</Eyebrow>
          {asset.column_lineage && Object.keys(asset.column_lineage).length ? (
            Object.entries(asset.column_lineage).map(
              ([column, dependencies]) => (
                <div
                  key={column}
                  className="flex flex-wrap items-baseline gap-2.5 border-b border-line-soft py-2 text-[13px]"
                >
                  <Mono className="w-[130px] shrink-0">{column}</Mono>
                  <span className="min-w-0 break-all text-muted-foreground">
                    {dependencies.length
                      ? dependencies
                          .map(
                            (dependency) =>
                              `${dependency.asset_key.join('/')}.${dependency.column_name}`,
                          )
                          .join(', ')
                      : 'No column dependencies declared'}
                  </span>
                </div>
              ),
            )
          ) : (
            <span className="text-[13px] text-muted-foreground">
              No column-level lineage has been observed.
            </span>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <Eyebrow>Reports that depend on this table</Eyebrow>
          {reports.map((report) => (
            <div
              key={report}
              className="border-b border-line-soft py-2 text-[13.5px]"
            >
              {report}
              <Badge variant="neutral" className="ml-2">
                Declared
              </Badge>
            </div>
          ))}
          <span className="text-[13px] text-muted-foreground">
            {reports.length
              ? 'Partial inventory from explicit phlo/reports metadata. Report health is not observed.'
              : 'No explicit report metadata supplied. Report inventory is unavailable.'}
          </span>
        </div>
      </div>
    </div>
  )
}

function SnapshotRollbackAction({
  data,
  selected,
  open,
  onOpenChange,
  onRolledBack,
}: {
  data: AssetSnapshots
  selected: AssetSnapshots['items'][number] | undefined
  open: boolean
  onOpenChange: (open: boolean) => void
  onRolledBack?: () => void
}) {
  const [confirmed, setConfirmed] = React.useState(false)
  const [state, setState] = React.useState<
    | { kind: 'idle' | 'pending' | 'complete' }
    | { kind: 'failed'; message: string }
  >({ kind: 'idle' })
  const submitting = React.useRef(false)
  const key = React.useRef<string | null>(null)
  async function rollback(event: React.FormEvent) {
    event.preventDefault()
    if (
      !confirmed ||
      !selected ||
      !data.table_name ||
      !data.metadata_location ||
      submitting.current ||
      state.kind === 'complete'
    )
      return
    submitting.current = true
    setState({ kind: 'pending' })
    try {
      const storageKey = `phlo:rollback:${data.env}:${data.nessie_ref}:${data.table_name}:${data.metadata_location}:${selected.snapshot_id}`
      key.current ??= sessionStorage.getItem(storageKey) ?? crypto.randomUUID()
      sessionStorage.setItem(storageKey, key.current)
      await rollbackAssetSnapshot({
        data: {
          env: data.env,
          table_name: data.table_name,
          snapshot_id: selected.snapshot_id,
          expected_metadata_location: data.metadata_location,
          nessie_ref: data.nessie_ref,
          idempotency_key: key.current,
          confirmed: true,
        },
      })
      setState({ kind: 'complete' })
    } catch (cause) {
      setState({
        kind: 'failed',
        message: cause instanceof Error ? cause.message : 'Rollback failed.',
      })
    } finally {
      submitting.current = false
    }
  }
  return (
    <>
      <Button
        variant="outline"
        className="h-10 shrink-0"
        disabled={
          !selected ||
          !data.current_snapshot_id ||
          selected.snapshot_id === data.current_snapshot_id ||
          !data.metadata_location ||
          !data.table_name
        }
        onClick={() => {
          setConfirmed(false)
          onOpenChange(true)
        }}
        title="Rollback requires table-write permission and recent verified MFA."
      >
        Roll back to this snapshot
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (state.kind !== 'pending') {
            onOpenChange(next)
            if (!next && state.kind === 'complete') onRolledBack?.()
          }
        }}
      >
        <DialogContent>
          <form
            onSubmit={(event) => void rollback(event)}
            className="flex min-h-0 flex-col"
          >
            <DialogHeader>
              <DialogTitle>Roll back to snapshot</DialogTitle>
              <DialogDescription>
                {data.table_name} · {data.env} · {data.nessie_ref}
              </DialogDescription>
            </DialogHeader>
            <DialogBody>
              <p className="text-sm">
                Restore snapshot <Mono>{selected?.snapshot_id}</Mono> on{' '}
                <Mono>{data.nessie_ref}</Mono>. This changes the current table
                data. Newer snapshots remain in history. A stale table revision
                or missing recent MFA will reject this request.
              </p>
              <CheckLine
                checked={confirmed}
                onCheckedChange={setConfirmed}
                disabled={state.kind === 'pending' || state.kind === 'complete'}
              >
                I confirm this snapshot rollback in {data.env} on{' '}
                {data.nessie_ref}.
              </CheckLine>
              {state.kind === 'failed' ? (
                <p role="alert" className="text-sm text-bad-text">
                  {state.message} Refresh history before starting a new request.
                </p>
              ) : null}
              {state.kind === 'complete' ? (
                <p role="status" className="text-sm">
                  The table now uses snapshot {selected?.snapshot_id}.
                </p>
              ) : null}
            </DialogBody>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={state.kind === 'pending'}
                onClick={() => {
                  onOpenChange(false)
                  if (state.kind === 'complete') onRolledBack?.()
                }}
              >
                {state.kind === 'complete' ? 'Refresh history' : 'Cancel'}
              </Button>
              <Button
                type="submit"
                variant="destructive"
                disabled={
                  !confirmed ||
                  state.kind === 'pending' ||
                  state.kind === 'complete'
                }
              >
                {state.kind === 'pending'
                  ? 'Rolling back…'
                  : 'Confirm rollback'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function SnapshotsTab({
  data,
  onRolledBack,
}: {
  data: AssetSnapshots
  onRolledBack?: () => void
}) {
  const snapshots = [...data.items].sort(
    (a, b) => b.timestamp_ms - a.timestamp_ms,
  )
  const latest = snapshots[0]
  const [selection, setSelection] = React.useState({
    id: data.current_snapshot_id ?? latest?.snapshot_id,
    revision: 0,
  })
  const selected = snapshots.find(
    (snapshot) => snapshot.snapshot_id === selection.id,
  )
  const [open, setOpen] = React.useState(false)
  const cols =
    'grid grid-cols-[230px_180px_100px_100px_70px_140px_minmax(0,1fr)] items-center gap-x-3.5'
  return (
    <div className={cn(pad, 'flex min-h-0 flex-col gap-[18px]')}>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Snapshots" value={snapshots.length} />
        <Stat
          label="Latest committed"
          value={
            latest
              ? new Date(latest.timestamp_ms).toLocaleString()
              : 'Not observed'
          }
        />
        <Stat
          label="Latest operation"
          value={latest?.operation ?? 'Not observed'}
        />
        <Stat label="Nessie ref" value={data.nessie_ref} />
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[1000px]" role="table" aria-label="Snapshots">
          <div
            role="row"
            className={cn(
              cols,
              'h-[34px] border-b border-line text-xs text-muted-foreground',
            )}
          >
            <span role="columnheader">Snapshot</span>
            <span role="columnheader">Committed</span>
            <span role="columnheader">Operation</span>
            <span role="columnheader" className="text-right">
              Rows added
            </span>
            <span role="columnheader" className="text-right">
              Files added
            </span>
            <span role="columnheader">Ref</span>
            <span role="columnheader">By</span>
          </div>
          {snapshots.map((snapshot) => (
            <div
              key={snapshot.snapshot_id}
              role="row"
              className={cn(
                cols,
                'h-11 border-b border-line-soft text-[13.5px]',
                selection.id === snapshot.snapshot_id && 'bg-branch-soft',
              )}
            >
              <span role="cell" className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={open}
                  onClick={() => {
                    setSelection((previous) => ({
                      id: snapshot.snapshot_id,
                      revision: previous.revision + 1,
                    }))
                  }}
                  aria-pressed={selection.id === snapshot.snapshot_id}
                  aria-label={`Select snapshot ${snapshot.snapshot_id}`}
                  className="font-mono text-[12.5px] underline underline-offset-4"
                >
                  {snapshot.snapshot_id}
                </button>
                {snapshot.snapshot_id === data.current_snapshot_id ? (
                  <Badge variant="neutral">Current</Badge>
                ) : null}
              </span>
              <span role="cell" className="text-[13px] text-muted-foreground">
                {new Date(snapshot.timestamp_ms).toLocaleString()}
              </span>
              <span role="cell">{snapshot.operation ?? 'Unknown'}</span>
              <span role="cell" className="text-right font-mono text-[12.5px]">
                {snapshot.summary['added-records'] ?? '—'}
              </span>
              <span
                role="cell"
                className="text-right font-mono text-[12.5px] text-muted-foreground"
              >
                {snapshot.summary['added-data-files'] ?? '—'}
              </span>
              <span role="cell">
                <Badge variant="neutral" className="font-mono">
                  {data.nessie_ref}
                </Badge>
              </span>
              <span role="cell" className="text-[13px] text-muted-foreground">
                {snapshot.author ?? 'Not observed'}
              </span>
            </div>
          ))}
        </div>
      </div>
      {!data.items.length ? <EmptyState title="No snapshots observed" /> : null}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <p className="m-0 min-w-0 flex-1 rounded-[10px] border border-border-card bg-sunken px-3.5 py-3 text-[13px] text-muted-foreground">
          Snapshots observed on {data.nessie_ref}. Current snapshot:{' '}
          <Mono>{data.current_snapshot_id ?? 'Not observed'}</Mono>. Select a
          row to inspect its metadata or roll back.
        </p>
        <SnapshotRollbackAction
          key={selection.revision}
          data={data}
          selected={selected}
          open={open}
          onOpenChange={setOpen}
          onRolledBack={onRolledBack}
        />
      </div>
      {selected ? (
        <div className="flex min-w-0 flex-col gap-3">
          <KeyValues
            items={[
              ['Selected snapshot', <Mono>{selected.snapshot_id}</Mono>],
              [
                'Parent snapshot',
                <Mono>{selected.parent_id ?? 'Not observed'}</Mono>,
              ],
              ['Schema', selected.schema_id ?? 'Not observed'],
              ['Sequence', selected.sequence_number ?? 'Not observed'],
              ['Author', selected.author ?? 'Not observed'],
              [
                'Manifest list',
                <Mono className="break-all">
                  {selected.manifest_list ?? 'Not observed'}
                </Mono>,
              ],
              [
                'Table revision',
                <Mono className="break-all">
                  {data.metadata_location ?? 'Not observed'}
                </Mono>,
              ],
            ]}
          />
          <details className="rounded-lg border border-line p-3 text-sm">
            <summary className="cursor-pointer">Full snapshot summary</summary>
            <dl className="mt-3 grid grid-cols-[minmax(120px,1fr)_minmax(0,2fr)] gap-2 break-all text-[13px]">
              {Object.entries(selected.summary).map(([name, value]) => (
                <React.Fragment key={name}>
                  <dt className="text-muted-foreground">{name}</dt>
                  <dd className="font-mono">{value}</dd>
                </React.Fragment>
              ))}
            </dl>
          </details>
        </div>
      ) : null}
    </div>
  )
}

function checkOutcome(passed: boolean | null) {
  if (passed === null)
    return {
      label: 'Not evaluated',
      tone: 'neutral' as const,
      bar: 'bg-border',
    }
  return passed
    ? { label: 'Passed', tone: 'ok' as const, bar: 'bg-ok-bar' }
    : { label: 'Failed', tone: 'bad' as const, bar: 'bg-bad' }
}

function AuditRow({
  check,
  executions,
}: {
  check: AssetChecks['definitions'][number]
  executions: AssetChecks['executions']
}) {
  const history = executions
    .filter((execution) => execution.check_name === check.name)
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
    .slice(0, 20)
  const latest = history[0]
  const outcome = checkOutcome(latest?.passed ?? null)
  return (
    <div
      role="row"
      className="grid h-[50px] grid-cols-[28px_minmax(180px,1fr)_110px_150px_260px_120px] items-center gap-x-3.5 border-b border-line-soft"
    >
      <span role="cell">
        <Dot tone={outcome.tone} className="size-2.5" />
      </span>
      <span
        role="cell"
        className="truncate font-mono text-[12.5px]"
        title={check.description ?? check.name}
      >
        {check.name}
      </span>
      <span role="cell" className="text-[13px] text-muted-foreground">
        Asset check
      </span>
      <span role="cell" className="text-[13.5px]" title={latest?.timestamp}>
        {latest ? outcome.label : 'No execution'}
      </span>
      <span
        role="cell"
        className="flex gap-[3px]"
        aria-label={`${history.length} recorded check executions, newest first`}
      >
        {history.map((execution) => (
          <span
            key={`${execution.run_id}:${execution.timestamp}`}
            role="img"
            aria-label={`${execution.timestamp}: ${checkOutcome(execution.passed).label}`}
            className={cn(
              'h-[18px] w-[9px] rounded-[2px]',
              checkOutcome(execution.passed).bar,
            )}
          />
        ))}
      </span>
      <span role="cell" className="text-[13px] text-muted-foreground">
        Not supplied
      </span>
    </div>
  )
}

export function AuditsTab({
  data,
  addAudit,
}: {
  data: AssetChecks
  addAudit: ReactNode
}) {
  return (
    <div className={cn(pad, 'flex flex-col gap-4')}>
      <div className="flex flex-wrap items-center">
        <div>
          <h3 className="m-0 text-[15px] font-medium">Asset checks</h3>
          <span className="text-[13px] text-muted-foreground">
            {data.definitions.length} defined checks
          </span>
        </div>
        <div className="ml-auto">{addAudit}</div>
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[900px]" role="table" aria-label="Audits">
          <div
            role="row"
            className="grid h-[34px] grid-cols-[28px_minmax(180px,1fr)_110px_150px_260px_120px] items-center gap-x-3.5 border-b border-line text-xs text-muted-foreground"
          >
            {[
              'Status',
              'Audit',
              'Kind',
              'Last result',
              'Last 20 recorded executions',
              'Blocks',
            ].map((label, index) => (
              <span role="columnheader" key={label}>
                {index === 0 ? <span className="sr-only">{label}</span> : label}
              </span>
            ))}
          </div>
          {data.definitions.map((check) => (
            <AuditRow
              key={check.name}
              check={check}
              executions={data.executions}
            />
          ))}
        </div>
      </div>
      {!data.definitions.length ? (
        <EmptyState title="No checks defined">
          This is not a passing audit result.
        </EmptyState>
      ) : null}
      <p className="m-0 text-[13px] text-muted-foreground">
        History shows recorded check executions. Blocking and warning policies
        are not supplied by this API.
      </p>
    </div>
  )
}
