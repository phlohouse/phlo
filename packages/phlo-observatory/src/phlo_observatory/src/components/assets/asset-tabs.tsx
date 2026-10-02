/** Renders asset tabs from observed metadata, query results, and check evidence. */
import { Link } from '@tanstack/react-router'
import { DatabaseIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import type {
  ApiAssetDetail,
  AssetChecks,
  AssetPreview,
  AssetSchemaHistory,
  AssetSnapshots,
} from '@/lib/data/api/assets'
import type { Env, LineageColumn } from '@/lib/data/types'
import { LineageGraph } from '@/components/assets/lineage-graph'
import { Stat } from '@/components/phlo/kpi'
import { Eyebrow, KeyValues } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Dot, Mono } from '@/components/phlo/status'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const pad = 'px-4 py-5 lg:px-7 lg:py-6'

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
            <div className="min-w-[560px]">
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
  data,
}: {
  asset: ApiAssetDetail
  data: AssetPreview
}) {
  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 text-[13px] text-muted-foreground lg:px-7">
        <DatabaseIcon className="size-4" /> Preview on{' '}
        <Mono>{data.nessie_ref}</Mono>
      </div>
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
              {data.columns.map((column) => (
                <th
                  key={column.name}
                  className="border-b border-line px-3 text-left font-normal"
                >
                  {column.name}{' '}
                  <span className="text-[10.5px] text-faint">
                    {column.type ?? 'unknown'}
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
                  ...data.columns.map((column) => row[column.name]),
                ].map((value, cell) => (
                  <td
                    key={cell}
                    className="h-8 border-r border-b border-line-soft px-3 whitespace-nowrap last:border-r-0"
                  >
                    {value === null ? (
                      <span className="text-faint">NULL</span>
                    ) : typeof value === 'object' ? (
                      JSON.stringify(value)
                    ) : (
                      String(value ?? '')
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
    <div className="grid min-h-0 grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)]">
      <section className="flex flex-col gap-1 border-b border-line px-4 py-5 lg:border-r lg:border-b-0">
        <Eyebrow className="pb-2">{data.items.length} versions</Eyebrow>
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
        edges={upstream.map((id) => [id, asset.id])}
        label={`${upstream.length} declared upstream assets feed ${asset.id}. Downstream inventory is unavailable.`}
      />
      <div className="grid grid-cols-1 gap-6 border-t border-line pt-4 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Eyebrow>Column-level use</Eyebrow>
          <span className="text-[13px] text-muted-foreground">
            Column-level lineage is not supplied by the API.
          </span>
        </div>
        <div className="flex flex-col gap-2">
          <Eyebrow>Reports that depend on this table</Eyebrow>
          <span className="text-[13px] text-muted-foreground">
            Downstream and report inventory are not supplied by the API.
          </span>
        </div>
      </div>
    </div>
  )
}

export function SnapshotsTab({ data }: { data: AssetSnapshots }) {
  const snapshots = [...data.items].sort(
    (a, b) => b.timestamp_ms - a.timestamp_ms,
  )
  const latest = snapshots[0]
  const cols =
    'grid grid-cols-[180px_160px_120px_100px_70px_150px_minmax(0,1fr)] items-center gap-x-3.5'
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
              'h-9 border-b border-line text-xs text-muted-foreground',
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
                'min-h-11 border-b border-line-soft text-[13px]',
              )}
            >
              <Mono>{snapshot.snapshot_id}</Mono>
              <span role="cell" className="text-xs text-muted-foreground">
                {new Date(snapshot.timestamp_ms).toLocaleString()}
              </span>
              <span role="cell">{snapshot.operation ?? 'Unknown'}</span>
              <span role="cell" className="text-right font-mono text-xs">
                {snapshot.summary['added-records'] ?? '—'}
              </span>
              <span role="cell" className="text-right font-mono text-xs">
                {snapshot.summary['added-data-files'] ?? '—'}
              </span>
              <span role="cell">
                <Badge variant="neutral" className="font-mono">
                  {data.nessie_ref}
                </Badge>
              </span>
              <span role="cell" className="text-xs text-muted-foreground">
                Not supplied
              </span>
            </div>
          ))}
        </div>
      </div>
      {!data.items.length ? <EmptyState title="No snapshots observed" /> : null}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <p className="m-0 min-w-0 flex-1 rounded-[10px] border border-border-card bg-sunken px-3.5 py-3 text-[13px] text-muted-foreground">
          Snapshots observed on {data.nessie_ref}. Current-snapshot identity and
          author are not supplied by this endpoint.
        </p>
        <Button
          variant="outline"
          className="h-10 shrink-0"
          disabled
          title="Snapshot rollback is not available in Observatory."
        >
          Roll back to this snapshot
        </Button>
      </div>
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
            ].map((label) => (
              <span role="columnheader" key={label}>
                {label}
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
