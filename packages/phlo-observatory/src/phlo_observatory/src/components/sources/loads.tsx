/** Real load receipts and scoped ingestion runs with explicit provenance boundaries. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { DownloadIcon } from 'lucide-react'
import type { LoadObservation, LoadsPage } from '@/lib/data/api/loads'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

function statusLabel(status: string) {
  return status.replaceAll('_', ' ')
}
function statusVariant(status: string) {
  return status === 'loaded' || status === 'run_success'
    ? 'ok'
    : /fail|cancel/.test(status)
      ? 'bad'
      : 'neutral'
}
function formatBytes(bytes: number | null) {
  if (bytes === null) return 'Not recorded'
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KiB`
}
function exportObservations(data: LoadsPage, items: Array<LoadObservation>) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify({ ...data, items }, null, 2)], {
      type: 'application/json',
    }),
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `phlo-load-observations-${data.env}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

export function LoadsInventory({ data }: { data: LoadsPage }) {
  const [status, setStatus] = React.useState('all')
  const [source, setSource] = React.useState('all')
  const [search, setSearch] = React.useState('')
  const [selectedId, setSelectedId] = React.useState(
    data.items.find((item) => item.status === 'loaded')?.id ??
      data.items[0]?.id,
  )
  const items = data.items.filter(
    (item) =>
      (status === 'all' || item.status === status) &&
      (source === 'all' || item.source === source) &&
      [item.name, item.upload_filename, item.relation, item.run_id, item.ref]
        .join(' ')
        .toLowerCase()
        .includes(search.toLowerCase()),
  )
  const selected = items.find((item) => item.id === selectedId) ?? items[0]
  const statuses = [...new Set(data.items.map((item) => item.status))].sort()
  const sources = [...new Set(data.items.map((item) => item.source))].sort()
  return (
    <div className="grid min-h-0 min-w-0 flex-1 overflow-y-auto xl:grid-cols-[minmax(0,1fr)_340px]">
      <section aria-label="Load inventory" className="min-w-0 space-y-4 p-5">
        <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1 text-xs text-muted-foreground">
            Source
            <select
              value={source}
              onChange={(e) => setSource(e.target.value)}
              className="rounded-md border border-line bg-background px-3 py-2 text-sm text-foreground"
            >
              <option value="all">All sources</option>
              {sources.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            Status
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="rounded-md border border-line bg-background px-3 py-2 text-sm text-foreground"
            >
              <option value="all">All statuses</option>
              {statuses.map((value) => (
                <option key={value} value={value}>
                  {statusLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <label className="grid min-w-0 flex-1 basis-48 gap-1 text-xs text-muted-foreground">
            Find a file, table or run
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="min-w-0 rounded-md border border-line bg-background px-3 py-2 text-sm text-foreground"
            />
          </label>
          <Button
            variant="outline"
            onClick={() => exportObservations(data, items)}
          >
            <DownloadIcon className="size-4" /> Export observations
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {items.length} visible observations
          {data.truncated ? ' · bounded results, more may exist' : ''}. CSV
          receipts and ingestion runs have different grains; their row counts
          are not added together.
        </p>
        {items.length ? (
          <div className="max-w-full overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[780px] text-left text-xs">
              <thead className="border-b border-line bg-raised text-muted-foreground">
                <tr>
                  {[
                    'File or observation',
                    'Source',
                    'Observed',
                    'Rows',
                    'Size',
                    'Into',
                    'Status',
                  ].map((name) => (
                    <th key={name} className="px-3 py-3 font-normal">
                      {name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {items.map((item) => (
                  <tr
                    key={item.id}
                    className={
                      selected?.id === item.id
                        ? 'bg-primary-soft'
                        : 'hover:bg-raised'
                    }
                  >
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        onClick={() => setSelectedId(item.id)}
                        aria-pressed={selected?.id === item.id}
                        className="text-left text-link underline-offset-4 hover:underline"
                      >
                        <span className="block max-w-60 truncate font-mono">
                          {item.upload_filename ?? item.name}
                        </span>
                        <span className="text-[11px] text-muted-foreground">
                          {item.evidence_kind === 'ingestion_run'
                            ? 'Ingestion run · file not recorded'
                            : item.evidence_kind === 'csv_attempt'
                              ? 'Prior CSV attempt'
                              : 'Private CSV receipt'}
                        </span>
                      </button>
                    </td>
                    <td className="px-3 py-3">{item.source}</td>
                    <td className="px-3 py-3 whitespace-nowrap">
                      {item.observed_at
                        ? new Date(item.observed_at).toLocaleString()
                        : 'Not recorded'}
                      <span className="block text-[11px] text-muted-foreground">
                        {item.timestamp_basis?.replaceAll('_', ' ') ??
                          'Timestamp basis not recorded'}
                      </span>
                    </td>
                    <td className="px-3 py-3 tabular-nums">
                      {item.rows?.toLocaleString() ?? 'Not recorded'}
                    </td>
                    <td className="px-3 py-3 whitespace-nowrap">
                      {formatBytes(item.byte_count)}
                    </td>
                    <td className="max-w-48 truncate px-3 py-3 font-mono">
                      {item.relation ?? 'Not recorded'}
                    </td>
                    <td className="px-3 py-3">
                      <Badge variant={statusVariant(item.status)}>
                        {statusLabel(item.status)}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="rounded-lg border border-line p-6 text-sm">
            No authorized observations match these filters.
          </p>
        )}
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">
            Evidence coverage and limits
          </summary>
          <ul className="mt-3 list-disc space-y-2 pl-4">
            {data.limitations.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
          <p className="mt-3">
            Inventory read at {new Date(data.observed_at).toLocaleString()}.
          </p>
        </details>
      </section>
      <aside
        aria-label="Selected load evidence"
        className="min-w-0 space-y-4 border-t border-line bg-raised p-5 xl:border-t-0 xl:border-l"
      >
        {selected ? (
          <LoadEvidence item={selected} env={data.env} />
        ) : (
          <p className="text-sm text-muted-foreground">
            Choose an observation to inspect its recorded evidence.
          </p>
        )}
      </aside>
    </div>
  )
}
function LoadEvidence({ item, env }: { item: LoadObservation; env: string }) {
  const fields = [
    ['Original filename', item.upload_filename],
    ['Uploaded by', item.upload_actor],
    ['Uploaded at (not load time)', item.upload_created_at],
    ['Observation timestamp', item.observed_at],
    ['Timestamp basis', item.timestamp_basis?.replaceAll('_', ' ')],
    ['Run started', item.run_started_at],
    ['Run ended', item.run_ended_at],
    ['Source', item.source],
    ['Into', item.relation],
    ['Reference', item.ref],
    ['Rows', item.rows === null ? null : String(item.rows)],
    ['Row meaning', item.rows_basis],
    [
      'Original bytes',
      item.byte_count === null ? null : String(item.byte_count),
    ],
    ['Checksum', item.checksum],
    ['Run', item.run_id],
    ['Job', item.job_id],
    ['Reference hash', item.ref_hash],
    ['Snapshot', item.snapshot_id],
    [
      'Review revision',
      item.review_version === null ? null : String(item.review_version),
    ],
    ['Recipe digest', item.recipe_digest],
  ]
  return (
    <>
      <Badge variant={statusVariant(item.status)}>
        {statusLabel(item.status)}
      </Badge>
      <h2 className="break-all text-base font-medium">{item.name}</h2>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {item.detail}
      </p>
      <dl className="space-y-3 text-xs">
        {fields.map(([name, value]) => (
          <div key={name}>
            <dt className="text-muted-foreground">{name}</dt>
            <dd className="mt-1 break-all font-mono">
              {value ?? 'Not recorded'}
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap gap-3 border-t border-line pt-4 text-xs">
        <Link
          to="/assets/$assetId"
          params={{ assetId: item.asset_id }}
          search={{ env }}
          className="text-link"
        >
          Inspect asset
        </Link>
        {item.run_id &&
        item.job_id &&
        item.evidence_kind === 'ingestion_run' ? (
          <Link
            to="/pipelines/$jobName/runs/$runId"
            params={{ jobName: item.job_id, runId: item.run_id }}
            search={{ env }}
            className="text-link"
          >
            Open run
          </Link>
        ) : null}
        {item.upload_id ? (
          <Link
            to="/sources"
            search={{ env, tab: 'upload', upload: item.upload_id }}
            className="text-link"
          >
            Open private upload
          </Link>
        ) : null}
      </div>
    </>
  )
}
