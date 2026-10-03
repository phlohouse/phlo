/** Renders asset tabs from observed metadata, query results, and check evidence. */
import { Link } from '@tanstack/react-router'
import { CodeIcon, DatabaseIcon } from 'lucide-react'
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
import type { QuerySession } from '@/lib/data/api/query'
import type { Env, LineageColumn } from '@/lib/data/types'
import {
  getAssetDetail,
  getAssetPreview,
  materializationJob,
  rollbackAssetSnapshot,
  startAssetExactRowCount,
} from '@/lib/data/api/assets'
import { cancelQuery, getQuerySession } from '@/lib/data/api/query'
import { exactRowCountValue, isExactRowCountCurrent } from '@/lib/exactRowCount'
import { LineageGraph } from '@/components/assets/lineage-graph'
import { Eyebrow, KeyValues } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Dot, Mono } from '@/components/phlo/status'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
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
import { DataTableCommandFilterMenu } from '@/components/assets/asset-data-filter'

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
        <RowsPerRun asset={asset} env={env} jobs={jobs} />
        <div>
          <div className="flex flex-wrap items-baseline gap-2 pb-1.5">
            <Eyebrow>
              Schema · {asset.columns.length} columns ·{' '}
              {asset.schema_source ?? 'observed'}
            </Eyebrow>
            <span className="ml-auto text-[13px] text-muted-foreground">
              Contract: {asset.schema_contract ?? 'not declared'}
            </span>
          </div>
          <div
            role="table"
            aria-label="Observed asset schema"
            tabIndex={0}
            className="overflow-x-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                    {column.nullable == null
                      ? 'Unknown'
                      : column.nullable
                        ? 'Yes'
                        : 'No'}
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
        <AssetMetadata asset={asset} env={env} jobs={jobs} />
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
            {asset.downstream?.length ?? 0} downstream assets ·{' '}
            {asset.dependencies.length} upstream dependencies.
          </span>
        </div>
      </aside>
    </div>
  )
}

function RowsPerRun({
  asset,
  env,
  jobs,
}: {
  asset: ApiAssetDetail
  env: Env
  jobs: Array<{ id: string }>
}) {
  return (
    <section
      aria-label="Rows written per run"
      className="flex min-w-0 flex-col gap-2.5"
    >
      <Eyebrow>Rows written per run</Eyebrow>
      <p className="m-0 text-xs text-muted-foreground">
        Scoped successful runs. Write counts are not the current table row
        count.
      </p>
      {asset.materializations.length ? (
        <div className="max-h-48 overflow-y-auto rounded-lg border border-line">
          <div className="grid grid-cols-[minmax(0,1fr)_80px_80px] gap-3 border-b border-line px-3 py-2 text-xs text-muted-foreground">
            <span>Run</span>
            <span className="text-right">Written</span>
            <span className="text-right">Deleted</span>
          </div>
          {asset.materializations.map((run) => {
            const job = materializationJob(run.job_id, jobs)
            return (
              <div
                key={`${run.run_id}:${run.timestamp}`}
                className="grid grid-cols-[minmax(0,1fr)_80px_80px] items-center gap-3 border-b border-line-soft px-3 py-2 text-xs last:border-0"
              >
                <div className="min-w-0">
                  {job ? (
                    <Link
                      to="/pipelines/$jobName/runs/$runId"
                      params={{ jobName: job.id, runId: run.run_id }}
                      search={{ env }}
                      title={run.run_id}
                      className="block truncate font-mono"
                    >
                      {run.run_id}
                    </Link>
                  ) : (
                    <>
                      <Mono className="block truncate" title={run.run_id}>
                        {run.run_id}
                      </Mono>
                      <span className="block text-muted-foreground">
                        Job identity unavailable
                      </span>
                    </>
                  )}
                  <time
                    title={run.timestamp}
                    className="block truncate text-muted-foreground"
                  >
                    {run.timestamp}
                  </time>
                </div>
                <span className="text-right">
                  {run.rows_inserted?.toLocaleString() ?? 'Unknown'}
                </span>
                <span className="text-right">
                  {run.rows_deleted?.toLocaleString() ?? 'Unknown'}
                </span>
              </div>
            )
          })}
        </div>
      ) : (
        <p className="m-0 rounded-lg border border-dashed border-line p-4 text-sm text-muted-foreground">
          No scoped write-count evidence is available.
        </p>
      )}
    </section>
  )
}

function freshnessEvidenceLabel(asset: ApiAssetDetail) {
  if (asset.freshness_source === 'iceberg_snapshot')
    return 'Selected-ref Iceberg snapshot'
  if (asset.freshness_source === 'dagster_materialization')
    return 'Accepted Dagster materialization'
  return 'Not observed'
}

function freshnessObservedTime(asset: ApiAssetDetail) {
  if (!asset.freshness_observed_at) return 'Not observed'
  return (
    <time dateTime={asset.freshness_observed_at}>
      {asset.freshness_observed_at.replace(/\.\d+Z$/, 'Z')}
    </time>
  )
}

function freshnessSlaLabel(asset: ApiAssetDetail) {
  return asset.freshness_sla_seconds
    ? `${asset.freshness_sla_seconds / 3600} hours`
    : 'Not declared'
}

function AssetMetadata({
  asset,
  env,
  jobs,
}: React.ComponentProps<typeof OverviewTab>) {
  return (
    <>
      <KeyValues
        keyWidth={112}
        className="text-[13.5px] [&_dd]:break-all"
        items={[
          ['Owner', asset.owner ?? 'Not declared'],
          ['Source', asset.source_name ?? 'Not declared'],
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
          ['Declared SLA', freshnessSlaLabel(asset)],
          [
            'Rows',
            <ExactRowCount
              key={`${env}:${asset.id}`}
              asset={asset}
              env={env}
            />,
          ],
          [
            'Size',
            asset.size_bytes == null
              ? 'Not observed'
              : `${asset.size_bytes.toLocaleString()} bytes`,
          ],
          [
            'Sort order',
            asset.sort_order == null
              ? 'Not observed'
              : asset.sort_order.join(', ') || 'Unsorted',
          ],
          ['Relation', asset.relation ?? 'Not observed'],
          [
            'Last materialized',
            asset.last_materialization_at ? (
              <time
                dateTime={asset.last_materialization_at}
                title={asset.last_materialization_at}
              >
                {asset.last_materialization_at.replace(/\.\d+Z$/, 'Z')}
              </time>
            ) : (
              'Not observed'
            ),
          ],
          ['Freshness evidence', freshnessEvidenceLabel(asset)],
          ['Freshness observed at', freshnessObservedTime(asset)],
          [
            'Freshness reason',
            asset.freshness_reason?.replaceAll('_', ' ') ?? 'None',
          ],
          [
            'Current Iceberg snapshot',
            asset.current_snapshot_id ?? 'Not observed',
          ],
        ]}
      />
      {asset.table_metadata_error ? (
        <p role="status" className="m-0 text-xs text-muted-foreground">
          {asset.table_metadata_error}
        </p>
      ) : null}
    </>
  )
}

type ExactCountObservation = {
  id: string
  source: string
  observedAt: string
  snapshotId: string
  nessieRef: string
}

function isActiveExactCount(session: QuerySession | null): boolean {
  return (
    session != null &&
    ['queued', 'running', 'cancelling'].includes(session.status)
  )
}

function isCurrentExactCount(
  session: QuerySession | null,
  observation: ExactCountObservation | null,
  currentSnapshotId: string | null | undefined,
  verifiedAt: string | null,
): boolean {
  return (
    session?.status === 'completed' &&
    verifiedAt != null &&
    isExactRowCountCurrent(
      exactRowCountValue(session.result?.rows[0]?.row_count),
      currentSnapshotId,
      observation?.snapshotId ?? '',
    )
  )
}

function exactCountMessage({
  observation,
  session,
  currentSnapshotId,
  verifiedAt,
  exactCount,
}: {
  observation: ExactCountObservation
  session: QuerySession | null
  currentSnapshotId: string | null | undefined
  verifiedAt: string | null
  exactCount: string | null
}): string {
  switch (session?.status) {
    case 'completed':
      if (exactCount == null)
        return 'Count result did not contain an exact total'
      if (currentSnapshotId === observation.snapshotId && verifiedAt != null) {
        const source =
          observation.source === 'trino_count_star'
            ? 'Trino COUNT(*)'
            : observation.source
        return `${source}: snapshot matched at ${new Date(verifiedAt).toLocaleString()}`
      }
      if (currentSnapshotId === observation.snapshotId)
        return `Observed count ${exactCount}; snapshot verification pending; not current`
      return `Observed count ${exactCount}; ${currentSnapshotId ? 'snapshot changed; not current' : 'current snapshot identity unavailable; not current'}`
    case 'failed':
      return `Count failed: ${session.error ?? 'provider unavailable'}`
    case 'cancelled':
      return 'Count cancelled'
    default:
      return 'Exact count running'
  }
}

function ExactCountEvidence({
  observation,
  session,
  currentSnapshotId,
  verifiedAt,
}: {
  observation: ExactCountObservation | null
  session: QuerySession | null
  currentSnapshotId: string | null | undefined
  verifiedAt: string | null
}) {
  if (!observation) return null
  const exactCount = exactRowCountValue(session?.result?.rows[0]?.row_count)
  const message = exactCountMessage({
    observation,
    session,
    currentSnapshotId,
    verifiedAt,
    exactCount,
  })
  return (
    <span className="text-xs text-muted-foreground">
      {message}
      {' · ref '}
      {observation.nessieRef}
      {' · snapshot '}
      {observation.snapshotId}
      {' · '}
      {new Date(observation.observedAt).toLocaleString()}
    </span>
  )
}

function ExactCountControl({
  active,
  canCount,
  submitting,
  onCancel,
  onCount,
}: {
  active: boolean
  canCount: boolean
  submitting: boolean
  onCancel: () => void
  onCount: () => void
}) {
  if (active) {
    return (
      <Button size="sm" variant="outline" onClick={onCancel}>
        Cancel count
      </Button>
    )
  }
  if (!canCount) return null
  return (
    <Button size="sm" variant="outline" disabled={submitting} onClick={onCount}>
      {submitting ? 'Starting…' : 'Count exact rows'}
    </Button>
  )
}

function ExactRowCount({ asset, env }: { asset: ApiAssetDetail; env: Env }) {
  const [observation, setObservation] =
    React.useState<ExactCountObservation | null>(null)
  const [session, setSession] = React.useState<QuerySession | null>(null)
  const [currentSnapshotId, setCurrentSnapshotId] = React.useState(
    asset.current_snapshot_id,
  )
  const [snapshotVerifiedAt, setSnapshotVerifiedAt] = React.useState<
    string | null
  >(null)
  const [error, setError] = React.useState<string | null>(null)
  const [submitting, setSubmitting] = React.useState(false)

  React.useEffect(() => {
    if (!observation || (session && !isActiveExactCount(session))) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await getQuerySession({
          data: { env, id: observation.id },
        })
        if (!active) return
        setError(null)
        setSession(next)
        if (['queued', 'running', 'cancelling'].includes(next.status)) {
          timer = setTimeout(poll, 800)
        }
      } catch {
        if (active) {
          setError('Count session check failed; retrying status lookup.')
          timer = setTimeout(poll, 1500)
        }
      }
    }
    timer = setTimeout(poll, 500)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [env, observation, session])

  React.useEffect(() => {
    setCurrentSnapshotId(asset.current_snapshot_id)
  }, [asset.current_snapshot_id])

  React.useEffect(() => {
    if (!observation || session?.status !== 'completed') return
    let active = true
    void getAssetDetail({ data: { env, id: asset.id, tab: 'overview' } })
      .then((detail) => {
        if (active) {
          setCurrentSnapshotId(detail.asset.current_snapshot_id)
          setSnapshotVerifiedAt(new Date().toISOString())
        }
      })
      .catch(() => {
        if (active) {
          setCurrentSnapshotId(null)
          setSnapshotVerifiedAt(null)
        }
      })
    return () => {
      active = false
    }
  }, [asset.id, env, observation, session?.status])

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const result = await startAssetExactRowCount({
        data: { env, id: asset.id },
      })
      setObservation({
        id: result.query.id,
        source: result.source,
        observedAt: result.observed_at,
        snapshotId: result.snapshot_id,
        nessieRef: result.nessie_ref,
      })
      setSnapshotVerifiedAt(null)
      setSession(null)
    } catch {
      setError(
        'Exact count is unavailable. Check query-provider support and access.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  const cancel = async () => {
    if (!observation) return
    try {
      setSession(await cancelQuery({ data: { env, id: observation.id } }))
      setError(null)
    } catch {
      setError('Count cancellation could not be confirmed.')
    }
  }

  const active =
    observation != null && (session == null || isActiveExactCount(session))
  const countIsCurrent = isCurrentExactCount(
    session,
    observation,
    currentSnapshotId,
    snapshotVerifiedAt,
  )
  const canCount = asset.row_count == null || observation != null

  return (
    <span className="flex min-w-0 flex-col items-start gap-1 break-normal [overflow-wrap:anywhere]">
      <span>
        {countIsCurrent
          ? exactRowCountValue(session?.result?.rows[0]?.row_count)
          : (asset.row_count?.toLocaleString() ?? 'Not observed')}
      </span>
      <ExactCountEvidence
        observation={observation}
        session={session}
        currentSnapshotId={currentSnapshotId}
        verifiedAt={snapshotVerifiedAt}
      />
      {error ? (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      ) : null}
      <ExactCountControl
        active={Boolean(active)}
        canCount={canCount}
        submitting={submitting}
        onCancel={cancel}
        onCount={() => void submit()}
      />
    </span>
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
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [filterOpen, setFilterOpen] = React.useState(false)
  const requestId = React.useRef(0)
  React.useEffect(
    () => () => {
      requestId.current += 1
    },
    [],
  )
  async function apply(next: Array<PreviewFilter>): Promise<boolean> {
    const sequence = ++requestId.current
    setPending(true)
    setError(null)
    try {
      const result = await getAssetPreview({
        data: { env: initialData.env, id: asset.id, filters: next },
      })
      if (sequence !== requestId.current) return false
      setData(result)
      setFilters(next)
      return true
    } catch (cause) {
      if (sequence === requestId.current)
        setError(cause instanceof Error ? cause.message : 'Preview failed.')
      return false
    } finally {
      if (sequence === requestId.current) setPending(false)
    }
  }
  return (
    <div className="flex min-h-0 flex-col" aria-busy={pending}>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3 text-[13px] text-muted-foreground lg:px-7">
        <DataTableCommandFilterMenu
          columns={data.columns}
          filters={filters}
          pending={pending}
          errorMessage={error}
          onApply={apply}
          onValidationError={() => setError('Enter a valid filter value.')}
          onOpenChange={setFilterOpen}
        />
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
      {error && !filterOpen ? (
        <p role="alert" className="px-4 text-sm text-bad-text lg:px-7">
          {error} The previous preview is unchanged.
        </p>
      ) : null}
      <div
        tabIndex={0}
        role="region"
        aria-label={`Sample rows from ${asset.id}; scroll horizontally to view all columns`}
        className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
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
                  <span className="text-[10.5px] text-muted-foreground">
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
                      <span className="text-muted-foreground">NULL</span>
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
                <Mono>{data.table_name}</Mono> · environment{' '}
                <Mono>{data.env}</Mono> · Nessie ref{' '}
                <Mono>{data.nessie_ref}</Mono>
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
                I confirm this snapshot rollback in {data.env} using Nessie ref{' '}
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
  const current = snapshots.find(
    (snapshot) => snapshot.snapshot_id === data.current_snapshot_id,
  )
  const [open, setOpen] = React.useState(false)
  const cols =
    'grid grid-cols-[230px_180px_100px_100px_70px_140px_minmax(0,1fr)] items-center gap-x-3.5'
  return (
    <div className={cn(pad, 'flex min-h-0 flex-col gap-[18px]')}>
      <SnapshotFacts
        data={data}
        snapshots={snapshots}
        current={current}
        latest={latest}
        selected={selected}
        selectionRevision={selection.revision}
        open={open}
        onOpenChange={setOpen}
        onRolledBack={onRolledBack}
      />
      <div className="text-xs text-muted-foreground">
        Select a row to inspect its metadata or request a guarded rollback.
      </div>
      <div
        tabIndex={0}
        role="region"
        aria-label="Snapshot history; scroll horizontally to view all columns"
        className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <div className="min-w-[1000px]" role="table" aria-label="Snapshots">
          <div
            role="row"
            className={cn(
              cols,
              'h-[34px] border-b border-line text-xs text-muted-foreground',
            )}
          >
            <span role="columnheader">Snapshot</span>
            <span role="columnheader">Committed (UTC)</span>
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
                <SnapshotTimestamp timestampMs={snapshot.timestamp_ms} />
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

function SnapshotFacts({
  data,
  snapshots,
  current,
  latest,
  selected,
  selectionRevision,
  open,
  onOpenChange,
  onRolledBack,
}: {
  data: AssetSnapshots
  snapshots: Array<AssetSnapshots['items'][number]>
  current: AssetSnapshots['items'][number] | undefined
  latest: AssetSnapshots['items'][number] | undefined
  selected: AssetSnapshots['items'][number] | undefined
  selectionRevision: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onRolledBack?: () => void
}) {
  return (
    <div className="grid gap-4 rounded-xl border border-border-card bg-raised p-4 sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-center">
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-xs text-muted-foreground">Current snapshot</span>
        <Mono className="truncate text-[13px]">
          {data.current_snapshot_id ?? 'Not observed'}
        </Mono>
        <span className="text-xs text-muted-foreground">
          {current ? (
            <SnapshotTimestamp timestampMs={current.timestamp_ms} />
          ) : (
            'Current snapshot time not observed'
          )}
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-xs text-muted-foreground">Observed history</span>
        <span className="text-[13px]">
          {snapshots.length} {snapshots.length === 1 ? 'snapshot' : 'snapshots'}
        </span>
        <span className="truncate text-xs text-muted-foreground">
          {latest ? (
            <>
              Latest observed · {latest.operation ?? 'operation unknown'} ·{' '}
              <SnapshotTimestamp timestampMs={latest.timestamp_ms} />
            </>
          ) : (
            'No snapshot history observed'
          )}
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-xs text-muted-foreground">Provenance</span>
        <span className="text-[13px]">Iceberg snapshot metadata</span>
        <span className="text-xs text-muted-foreground">
          <span className="block">
            Nessie ref <Mono>{data.nessie_ref}</Mono>
          </span>
          {data.table_name ? (
            <span className="block break-all">
              Table <Mono>{data.table_name}</Mono>
            </span>
          ) : null}
        </span>
      </div>
      <div className="flex flex-col items-start gap-1">
        <SnapshotRollbackAction
          key={selectionRevision}
          data={data}
          selected={selected}
          open={open}
          onOpenChange={onOpenChange}
          onRolledBack={onRolledBack}
        />
        <span className="max-w-44 text-[11px] text-muted-foreground">
          Requires table-write access and recent verified MFA.
        </span>
      </div>
    </div>
  )
}

export function formatSnapshotTimestamp(timestampMs: number): string {
  if (!Number.isFinite(new Date(timestampMs).getTime()))
    return 'Timestamp unavailable'
  return `${new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(timestampMs)} UTC`
}

function SnapshotTimestamp({ timestampMs }: { timestampMs: number }) {
  const date = new Date(timestampMs)
  if (!Number.isFinite(date.getTime())) return <>Timestamp unavailable</>
  const exact = date.toISOString()
  return (
    <time dateTime={exact} title={exact}>
      {formatSnapshotTimestamp(timestampMs)}
    </time>
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
  historyUnavailable,
}: {
  check: AssetChecks['definitions'][number]
  executions: AssetChecks['executions']
  historyUnavailable: boolean
}) {
  const history = historyUnavailable
    ? []
    : executions
        .filter((execution) => execution.check_name === check.name)
        .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
        .slice(0, 20)
  const latest = history[0]
  const outcome = historyUnavailable
    ? {
        label: 'History unavailable',
        tone: 'neutral' as const,
        bar: 'bg-border',
      }
    : checkOutcome(latest?.passed ?? null)
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
        {historyUnavailable
          ? 'History unavailable'
          : latest
            ? outcome.label
            : 'No execution'}
      </span>
      <span
        role="cell"
        className="flex gap-[3px]"
        aria-label={
          historyUnavailable
            ? 'Check execution history unavailable'
            : `${history.length} recorded check executions, newest first`
        }
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
              historyUnavailable={data.history.unverifiable_checks.includes(
                check.name,
              )}
            />
          ))}
        </div>
      </div>
      {!data.definitions.length ? (
        <EmptyState title="No checks defined">
          This is not a passing audit result.
        </EmptyState>
      ) : null}
      {data.history.status === 'partial' ? (
        <p role="status" className="m-0 text-[13px] text-muted-foreground">
          Some execution history could not be verified for this repository and
          ref. Affected check history is hidden and is not a passing result.
        </p>
      ) : null}
      <p className="m-0 text-[13px] text-muted-foreground">
        History shows recorded check executions. Blocking and warning policies
        are not supplied by this API.
      </p>
    </div>
  )
}
