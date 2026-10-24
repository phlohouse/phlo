/** Private retained uploads stay discoverable after leaving the upload wizard. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import type { CsvUpload, CsvUploadSummary } from '@/lib/data/api/uploads'
import type { UploadCandidateReview } from '@/lib/data/api/upload-candidate'
import {
  getUploadHandoff,
  reconcileUploadCandidate,
  reviewUploadCandidate,
} from '@/lib/data/api/upload-candidate'
import {
  ArchiveEvidenceAction,
  EvidenceStatus,
  SourceHandoffPanel,
} from '@/components/phlo/retained-evidence'
import { archiveCsvUpload, reclaimCsvUpload } from '@/lib/data/api/uploads'
import { ActionButton as Button } from '@/components/phlo/action-button'
import { ReclaimAction } from '@/components/phlo/reclaim-action'
import { ActionGroup } from '@/components/ui/action-group'
import { Badge } from '@/components/ui/badge'

export function UploadInventory({
  env,
  items,
  total,
  archived,
  onArchived,
  onReclaimed,
}: {
  env: string
  items: Array<CsvUploadSummary>
  total: number
  archived: boolean
  onArchived: (value: boolean) => void
  onReclaimed: () => void
}) {
  return (
    <section
      aria-label="Retained uploads"
      className="min-h-0 space-y-4 overflow-auto p-5"
    >
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={archived}
          onChange={(event) => onArchived(event.target.checked)}
        />
        Include archived uploads
      </label>
      <p className="text-xs text-muted-foreground">
        {items.length} of {total} private retained files. Upload time is not
        load time. Archiving retains payloads; explicit reclamation removes only
        local payloads, not provider data.
      </p>
      {items.length ? (
        <ul className="grid list-none gap-3 p-0">
          {items.map((upload) => (
            <li
              key={upload.upload_id}
              className="grid min-w-0 gap-2 rounded-lg border border-line p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                {upload.reclaimed_at || upload.reclaim_started_at ? (
                  <span className="break-all text-sm">{upload.filename}</span>
                ) : (
                  <Link
                    to="/sources"
                    search={{ env, tab: 'upload', upload: upload.upload_id }}
                    className="break-all text-sm text-link"
                  >
                    {upload.filename}
                  </Link>
                )}
                <Badge variant="neutral">
                  {upload.reclaimed_at
                    ? 'Payload reclaimed'
                    : upload.reclaim_started_at
                      ? 'Reclamation started'
                      : upload.archived_at
                        ? 'Archived'
                        : upload.status}
                </Badge>
              </div>
              <p className="m-0 text-xs text-muted-foreground">
                Uploaded {new Date(upload.created_at).toLocaleString()} by{' '}
                {upload.actor} · {upload.byte_count.toLocaleString()} bytes
              </p>
              <details className="text-xs">
                <summary className="cursor-pointer">File provenance</summary>
                <p className="break-all font-mono">SHA-256 {upload.checksum}</p>
                <p>
                  {upload.archived_at
                    ? `Archived ${new Date(upload.archived_at).toLocaleString()} by ${upload.archived_by}: ${upload.archive_reason}`
                    : 'Original bytes retained'}
                </p>
              </details>
              {upload.reclaimed_at || upload.reclaim_started_at ? (
                <>
                  <p className="m-0 text-xs text-muted-foreground">
                    {upload.reclaimed_at
                      ? `${upload.reclaimed_bytes ?? 0} local bytes reclaimed by ${upload.reclaimed_by}. ${upload.reclaim_reason}`
                      : 'Reclamation started. Original file review and restoration are unavailable.'}{' '}
                    Identity, hashes, source handoff and operation receipts
                    remain. Provider data is unchanged.
                  </p>
                  <UploadReclamation
                    upload={upload}
                    env={env}
                    disabled={false}
                    onReclaimed={onReclaimed}
                  />
                  <HistoricalUploadReview
                    env={env}
                    uploadId={upload.upload_id}
                  />
                </>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="rounded-lg border border-line p-6 text-sm text-muted-foreground">
          No retained uploads in this view.
        </p>
      )}
    </section>
  )
}

function UploadReclamation({
  upload,
  env,
  disabled,
  onReclaimed,
}: {
  upload: CsvUploadSummary
  env: string
  disabled: boolean
  onReclaimed: () => void
}) {
  if (!upload.archived_at || upload.reclaimed_at) return null
  return (
    <ReclaimAction
      identity={upload.upload_id}
      label={
        upload.reclaim_started_at
          ? 'Retry payload reclamation'
          : 'Reclaim file payload'
      }
      description="Permanently remove this archived upload's local CSV bytes and saved row payload. Identity, hashes, source handoff and operation receipts remain. This does not delete provider tables or catalogs, or release mutation fences."
      operation="v1_csv_upload_reclaim"
      disabled={disabled}
      onReclaim={async (reason) => {
        await reclaimCsvUpload({
          data: {
            env,
            upload_id: upload.upload_id,
            confirmed: true,
            reason,
            expected_checksum: upload.checksum,
          },
        })
        onReclaimed()
      }}
    />
  )
}

export function UploadRetention({
  upload,
  env,
  disabled,
  onChanged,
  onReclaimed,
}: {
  upload: CsvUpload
  env: string
  disabled: boolean
  onChanged: (value: CsvUpload) => void
  onReclaimed: () => void
}) {
  return (
    <div className="grid gap-2">
      <ActionGroup>
        <ArchiveEvidenceAction
          archived={Boolean(upload.archived_at)}
          reason={upload.archive_reason}
          operation="v1_csv_upload_archive"
          disabled={
            disabled ||
            Boolean(upload.reclaimed_at || upload.reclaim_started_at)
          }
          onChange={async (input) => {
            const result = await archiveCsvUpload({
              data: { env, upload_id: upload.upload_id, ...input },
            })
            onChanged({
              ...upload,
              archived_at: result.archived_at,
              archived_by: result.archived_by,
              archive_reason: result.archive_reason,
            })
          }}
        />
        <UploadReclamation
          upload={upload}
          env={env}
          disabled={disabled}
          onReclaimed={onReclaimed}
        />
      </ActionGroup>
      {upload.archived_at ? (
        <p role="status" className="m-0 text-xs text-muted-foreground">
          Archived {new Date(upload.archived_at).toLocaleString()} by{' '}
          {upload.archived_by}. {upload.archive_reason} Restore before changing
          columns or loading a candidate.
        </p>
      ) : null}
    </div>
  )
}

export function HistoricalUploadReview({
  env,
  uploadId,
}: {
  env: string
  uploadId: string
}) {
  const [review, setReview] = React.useState<UploadCandidateReview>()
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  async function read(reconcile: boolean) {
    setBusy(true)
    setError('')
    try {
      setReview(
        await (reconcile ? reconcileUploadCandidate : reviewUploadCandidate)({
          data: { env, upload_id: uploadId },
        }),
      )
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Retained candidate evidence is unavailable.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <section
      aria-label="Historical candidate evidence"
      className="grid min-w-0 gap-3 rounded-lg border border-line p-4 text-xs"
    >
      <h2 className="m-0 text-sm font-medium">Historical candidate evidence</h2>
      <p className="m-0 text-muted-foreground">
        Read immutable receipts, including after reference drift. Reconciliation
        verifies durable evidence only: it never retries a load or changes
        provider data.
      </p>
      <ActionGroup>
        <Button
          variant="outline"
          operation="v1_csv_candidate_review"
          disabled={busy}
          onClick={() => void read(false)}
        >
          Review recorded candidate
        </Button>
        <Button
          variant="outline"
          operation="v1_csv_candidate_reconcile"
          disabled={busy}
          onClick={() => void read(true)}
        >
          Reconcile evidence
        </Button>
      </ActionGroup>
      {error ? (
        <p role="alert" className="m-0 text-bad-text">
          {error}
        </p>
      ) : null}
      {review ? (
        <>
          <p className="m-0 break-all font-mono">
            {review.receipt.status} · {review.receipt.candidate_ref} · run{' '}
            {review.receipt.run_id ?? 'Not recorded'}
          </p>
          <EvidenceStatus evidence={review.evidence} />
          {review.reconciliation ? (
            <p role="status" className="m-0 text-muted-foreground">
              Reconciliation:{' '}
              {review.reconciliation.resolution === 'verified_success'
                ? 'verified success'
                : 'unresolved'}
              . {review.reconciliation.reason}
            </p>
          ) : null}
          {review.preview ? (
            <details>
              <summary className="cursor-pointer">
                Pinned snapshot preview · {review.preview.row_count} rows
              </summary>
              <pre
                role="region"
                aria-label="Historical snapshot rows"
                tabIndex={0}
                className="max-h-64 max-w-full overflow-auto rounded bg-background p-3"
              >
                {JSON.stringify(review.preview.rows, null, 2)}
              </pre>
            </details>
          ) : (
            <p className="m-0 text-muted-foreground">
              No verified historical snapshot preview is available. No current
              rows are substituted.
            </p>
          )}
          <SourceHandoffPanel
            operation="v1_csv_candidate_handoff"
            load={() =>
              getUploadHandoff({ data: { env, upload_id: uploadId } })
            }
          />
        </>
      ) : null}
    </section>
  )
}
