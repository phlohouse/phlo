/** Reviewed source bytes flow into a separate candidate, preserving its base ref. */
import * as React from 'react'
import type { CsvUpload } from '@/lib/data/api/uploads'
import type {
  UploadCandidate,
  UploadCandidatePreview,
} from '@/lib/data/api/upload-candidate'
import {
  getUploadCandidate,
  loadUploadCandidate,
  planUploadCandidate,
  prepareUploadDestination,
  previewUploadCandidate,
} from '@/lib/data/api/upload-candidate'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

export function CsvDestination({
  env,
  upload,
  dirty,
}: {
  env: string
  upload: CsvUpload
  dirty: boolean
}) {
  const [table, setTable] = React.useState(
    `upload_${upload.upload_id.slice(0, 12)}`,
  )
  const [receipt, setReceipt] = React.useState<UploadCandidate | null>(null)
  const [preview, setPreview] = React.useState<UploadCandidatePreview | null>(
    null,
  )
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const identity = { env, upload_id: upload.upload_id }
  React.useEffect(() => {
    let live = true
    void getUploadCandidate({ data: { env, upload_id: upload.upload_id } })
      .then((value) => {
        if (live) {
          setReceipt(value)
          setTable(value.table)
        }
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [env, upload.upload_id])
  async function action(work: () => Promise<UploadCandidate>) {
    setBusy(true)
    setError('')
    try {
      setReceipt(await work())
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : 'Candidate operation failed.',
      )
    } finally {
      setBusy(false)
    }
  }
  async function readback() {
    setBusy(true)
    setError('')
    try {
      const result = await previewUploadCandidate({ data: identity })
      setReceipt(result.receipt)
      setPreview(result)
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Candidate readback is unavailable.',
      )
      try {
        setReceipt(await getUploadCandidate({ data: identity }))
      } catch {
        /* Keep the last verified receipt visible. */
      }
    } finally {
      setBusy(false)
    }
  }
  const valid = upload.status === 'reviewed' && !dirty
  return (
    <section
      className="min-w-0 max-w-full space-y-4 rounded-xl border border-line p-4"
      aria-label="CSV destination and load"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">3 Destination</h2>
        <Badge variant="neutral">Candidate only</Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        Create a new bronze table on a separate candidate branch. The reviewed
        recipe becomes immutable when prepared. No schedule, access grant or
        merge is created.
      </p>
      <label className="block space-y-1 text-xs">
        <span>New table in bronze</span>
        <input
          aria-label="New bronze table"
          value={table}
          disabled={busy || Boolean(receipt)}
          onChange={(event) => setTable(event.target.value)}
          pattern="[a-z][a-z0-9_]{0,62}"
          className="block w-full rounded-md border border-line bg-background px-3 py-2 font-mono"
        />
      </label>
      {!receipt || receipt.status === 'preparing' ? (
        <Button
          disabled={!valid || busy || !/^[a-z][a-z0-9_]{0,62}$/.test(table)}
          onClick={() =>
            void action(() =>
              prepareUploadDestination({
                data: { ...identity, expected_version: upload.version, table },
              }),
            )
          }
        >
          {busy ? 'Preparing…' : 'Prepare candidate destination'}
        </Button>
      ) : null}
      {!valid ? (
        <p className="text-xs text-muted-foreground">
          Save a valid column review before preparing a destination.
        </p>
      ) : null}
      <CandidateProgress
        receipt={receipt}
        env={env}
        upload={upload}
        busy={busy}
        valid={valid}
        action={action}
        readback={readback}
      />
      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-bad-line bg-bad-soft p-3 text-xs text-bad-ink"
        >
          {error}
        </p>
      ) : null}
      <CandidatePreview preview={preview} />
    </section>
  )
}

function CandidateProgress({
  receipt,
  env,
  upload,
  busy,
  valid,
  action,
  readback,
}: {
  receipt: UploadCandidate | null
  env: string
  upload: CsvUpload
  busy: boolean
  valid: boolean
  action: (work: () => Promise<UploadCandidate>) => Promise<void>
  readback: () => Promise<void>
}) {
  if (!receipt) return null
  const identity = { env, upload_id: upload.upload_id }
  return (
    <>
      <dl className="grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Candidate branch</dt>
          <dd className="break-all font-mono">{receipt.candidate_ref}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Destination</dt>
          <dd className="break-all font-mono">{receipt.relation}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Review</dt>
          <dd>
            Version {receipt.review_version} ·{' '}
            {receipt.recipe_digest.slice(0, 12)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">State</dt>
          <dd>{receipt.status}</dd>
        </div>
      </dl>
      <h2 className="text-sm font-semibold">4 Review and load</h2>
      {['prepared', 'planned', 'failed'].includes(receipt.status) ? (
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void action(() => planUploadCandidate({ data: identity }))
          }
        >
          {receipt.status === 'failed'
            ? 'Prepare verified retry plan'
            : receipt.plan_hash
              ? 'Refresh verified load plan'
              : 'Estimate one-off load'}
        </Button>
      ) : null}
      {receipt.plan_hash ? (
        <div className="rounded-lg border border-line p-3 text-xs">
          <p>
            One unpartitioned asset · {upload.row_count?.toLocaleString()}{' '}
            reviewed source rows
          </p>
          <p className="mt-1 break-all font-mono text-muted-foreground">
            Job {receipt.job_name} · snapshot{' '}
            {receipt.job_snapshot_id?.slice(0, 12)}
          </p>
          <p className="mt-1 break-all font-mono text-muted-foreground">
            Plan {receipt.plan_hash}
          </p>
        </div>
      ) : null}
      {receipt.status === 'planned' && receipt.plan_hash ? (
        <Button
          disabled={busy || !valid}
          onClick={() =>
            void action(() =>
              loadUploadCandidate({
                data: {
                  ...identity,
                  expected_version: receipt.review_version,
                  recipe_digest: receipt.recipe_digest,
                  plan_hash: receipt.plan_hash!,
                  confirmed: true,
                  idempotency_key: `csv-${upload.upload_id}-${receipt.recipe_digest.slice(0, 16)}`,
                  retry_of_run_id: receipt.attempts.at(-1)?.run_id ?? null,
                },
              }),
            )
          }
        >
          {receipt.attempts.length
            ? 'Confirm retry into candidate'
            : 'Confirm load into candidate'}
        </Button>
      ) : null}
      {receipt.attempts.length ? (
        <p className="text-xs text-muted-foreground">
          Prior failed attempts:{' '}
          {receipt.attempts.map((attempt) => attempt.run_id).join(', ')}.
          Retrying requires a new verified plan and explicit confirmation.
        </p>
      ) : null}
      {receipt.run_id ? (
        <div className="space-y-2 text-xs">
          <p className="break-all font-mono">Actual run: {receipt.run_id}</p>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void readback()}
          >
            {busy ? 'Checking…' : 'Check run and preview candidate'}
          </Button>
          <p className="text-muted-foreground">
            Loaded is shown only after the exact run succeeds and its actual
            candidate table is read back.
          </p>
        </div>
      ) : null}
    </>
  )
}
function CandidatePreview({
  preview,
}: {
  preview: UploadCandidatePreview | null
}) {
  if (!preview) return null
  return (
    <div className="min-w-0 max-w-full space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="ok">Candidate loaded</Badge>
        <span className="text-xs">
          {preview.row_count.toLocaleString()} actual rows · snapshot{' '}
          {preview.snapshot_id}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        Base reference {preview.receipt.base_ref}{' '}
        {preview.receipt.main_unchanged === true
          ? 'is unchanged from preparation.'
          : preview.receipt.main_unchanged === false
            ? 'changed independently since preparation; no merge was performed here.'
            : 'has not been checked for changes since preparation.'}{' '}
        Candidate remains separate.
      </p>
      <div className="min-w-0 max-w-full overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-left text-xs">
          <thead>
            <tr>
              {preview.columns.map((name) => (
                <th
                  key={name}
                  className="whitespace-nowrap border-b border-line px-3 py-2 font-medium"
                >
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, index) => (
              <tr key={index}>
                {preview.columns.map((name, column) => (
                  <td
                    key={column}
                    className="max-w-64 truncate border-b border-line px-3 py-2 font-mono"
                  >
                    {row[name] === null ? 'NULL' : String(row[name])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        First {preview.rows.length} rows read from the pinned candidate table.
        {preview.query_id ? ` Query ${preview.query_id}.` : ''}
      </p>
    </div>
  )
}
