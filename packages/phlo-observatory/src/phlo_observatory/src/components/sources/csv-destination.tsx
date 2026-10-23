/** Reviewed source bytes flow into a separate candidate, preserving its base ref. */
import * as React from 'react'
import { CheckCircle2Icon } from 'lucide-react'
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

function isLoaded(receipt: UploadCandidate | null): boolean {
  return Boolean(
    receipt?.status === 'loaded' &&
    receipt.loaded &&
    receipt.run_id &&
    receipt.snapshot_id &&
    receipt.row_count !== null,
  )
}

function useCandidate({ env, upload }: { env: string; upload: CsvUpload }) {
  const [table, setTable] = React.useState(
    `upload_${upload.upload_id.slice(0, 12)}`,
  )
  const [receipt, setReceipt] = React.useState<UploadCandidate | null>(null)
  const [preview, setPreview] = React.useState<UploadCandidatePreview | null>(
    null,
  )
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [checkingDestination, setCheckingDestination] = React.useState(true)
  const identity = { env, upload_id: upload.upload_id }
  const identityKey = `${env}:${upload.upload_id}`
  const currentIdentity = React.useRef(identityKey)
  currentIdentity.current = identityKey
  React.useEffect(() => {
    let live = true
    const matches = (value: UploadCandidate) =>
      value.env === env && value.upload_id === upload.upload_id
    setCheckingDestination(true)
    setReceipt(null)
    setPreview(null)
    setError('')
    setBusy(false)
    setTable(`upload_${upload.upload_id.slice(0, 12)}`)
    void (async () => {
      let value: UploadCandidate
      try {
        value = await getUploadCandidate({
          data: { env, upload_id: upload.upload_id },
        })
      } catch {
        if (live) setCheckingDestination(false)
        return
      }
      if (!live) return
      setCheckingDestination(false)
      if (!matches(value)) {
        setError('The destination response does not match this upload.')
        return
      }
      setReceipt(value)
      setTable(value.table)
      if (!isLoaded(value)) return
      setBusy(true)
      try {
        const result = await previewUploadCandidate({
          data: { env, upload_id: upload.upload_id },
        })
        if (!live) return
        if (!matches(result.receipt))
          throw new Error('The preview response does not match this upload.')
        setReceipt(result.receipt)
        setPreview(isLoaded(result.receipt) ? result : null)
      } catch (cause) {
        if (live)
          setError(
            cause instanceof Error
              ? cause.message
              : 'Candidate preview is unavailable. Refresh the preview to try again.',
          )
      } finally {
        if (live) setBusy(false)
      }
    })()
    return () => {
      live = false
    }
  }, [env, upload.upload_id])
  function accept(value: UploadCandidate) {
    if (value.env !== env || value.upload_id !== upload.upload_id)
      throw new Error('The destination response does not match this upload.')
    setReceipt(value)
  }
  async function action(work: () => Promise<UploadCandidate>) {
    setBusy(true)
    setError('')
    try {
      const result = await work()
      if (currentIdentity.current !== identityKey) return
      accept(result)
      setPreview(null)
    } catch (cause) {
      if (currentIdentity.current === identityKey)
        setError(
          cause instanceof Error
            ? cause.message
            : 'Candidate operation failed.',
        )
    } finally {
      if (currentIdentity.current === identityKey) setBusy(false)
    }
  }
  async function readback() {
    setBusy(true)
    setError('')
    try {
      const result = await previewUploadCandidate({ data: identity })
      if (currentIdentity.current !== identityKey) return
      accept(result.receipt)
      setPreview(isLoaded(result.receipt) ? result : null)
    } catch (cause) {
      if (currentIdentity.current !== identityKey) return
      setError(
        cause instanceof Error
          ? cause.message
          : 'Candidate readback is unavailable.',
      )
      try {
        const result = await getUploadCandidate({ data: identity })
        if (currentIdentity.current === identityKey) accept(result)
      } catch {
        /* Keep the last verified receipt visible. */
      }
    } finally {
      if (currentIdentity.current === identityKey) setBusy(false)
    }
  }
  return {
    table,
    setTable,
    receipt,
    preview,
    error,
    busy,
    checkingDestination,
    action,
    readback,
  }
}

type CandidateState = ReturnType<typeof useCandidate>
type DestinationProps = { env: string; upload: CsvUpload; dirty: boolean }

function stageHeading(receipt: UploadCandidate | null) {
  if (isLoaded(receipt)) return 'Candidate loaded'
  if (!receipt || receipt.status === 'preparing') return 'Destination and load'
  const headings = {
    queued: 'Load queued',
    running: 'Load running',
    failed: 'Load failed',
    prepared: 'Review and load',
    planned: 'Review and load',
    loaded: 'Check load status',
  }
  return headings[receipt.status]
}
function refreshLabel(busy: boolean, completed: boolean) {
  if (completed) return busy ? 'Refreshing preview…' : 'Refresh preview'
  return busy ? 'Checking load…' : 'Check load status'
}

export function CsvDestination(props: DestinationProps) {
  return (
    <CsvDestinationPanel
      key={`${props.env}:${props.upload.upload_id}`}
      {...props}
    />
  )
}

function CsvDestinationPanel({ env, upload, dirty }: DestinationProps) {
  const state = useCandidate({ env, upload })
  const { receipt, preview, error, busy, checkingDestination, readback } = state
  const completed = isLoaded(receipt)
  const valid = upload.status === 'reviewed' && !dirty
  return (
    <section
      className="min-w-0 max-w-full space-y-5 rounded-xl border border-line p-5"
      aria-label="CSV destination and load"
      aria-busy={busy || checkingDestination}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          {completed ? (
            <CheckCircle2Icon
              aria-hidden="true"
              className="size-5 text-ok-ink"
            />
          ) : null}
          {stageHeading(receipt)}
        </h2>
        <Badge variant={completed ? 'ok' : 'neutral'}>Separate candidate</Badge>
      </div>
      <CandidateStage state={state} env={env} upload={upload} valid={valid} />
      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-bad-line bg-bad-soft p-3 text-sm text-bad-ink"
        >
          {error}
        </p>
      ) : null}
      {!checkingDestination && receipt?.run_id ? (
        <Button
          operation="v1_csv_candidate_preview"
          variant="outline"
          disabled={busy}
          onClick={() => void readback()}
        >
          {refreshLabel(busy, completed)}
        </Button>
      ) : null}
      {completed && preview ? (
        <CandidatePreview preview={preview} upload={upload} />
      ) : null}
      {receipt ? (
        <CandidateEvidence receipt={receipt} preview={preview} />
      ) : null}
    </section>
  )
}

function CandidateStage({
  state,
  env,
  upload,
  valid,
}: {
  state: CandidateState
  env: string
  upload: CsvUpload
  valid: boolean
}) {
  const { receipt, checkingDestination, busy, action } = state
  if (checkingDestination)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Checking destination…
      </p>
    )
  if (isLoaded(receipt) && receipt)
    return (
      <div className="space-y-2">
        <p className="text-sm">
          <strong>{receipt.row_count?.toLocaleString()} actual rows</strong> in{' '}
          <span className="break-all font-mono">{receipt.relation}</span>.
        </p>
        <BaseReference receipt={receipt} />
      </div>
    )
  if (!receipt || receipt.status === 'preparing')
    return (
      <DestinationSetup state={state} env={env} upload={upload} valid={valid} />
    )
  return (
    <CandidateProgress
      receipt={receipt}
      upload={upload}
      busy={busy}
      valid={valid}
      action={action}
      env={env}
    />
  )
}

function DestinationSetup({
  state,
  env,
  upload,
  valid,
}: {
  state: CandidateState
  env: string
  upload: CsvUpload
  valid: boolean
}) {
  const { table, setTable, busy, receipt, action } = state
  return (
    <div className="space-y-4">
      <p className="max-w-prose text-sm text-muted-foreground">
        Prepare a new bronze table on a separate candidate branch. Your saved
        column review will be locked for this load. The base reference stays
        separate; nothing is scheduled or merged.
      </p>
      <label className="block max-w-lg space-y-1.5 text-sm">
        <span>New table in bronze</span>
        <input
          aria-label="New bronze table"
          value={table}
          disabled={busy || Boolean(receipt)}
          onChange={(event) => setTable(event.target.value)}
          pattern="[a-z][a-z0-9_]{0,62}"
          className="block w-full rounded-md border border-line bg-background px-3 py-2 font-mono text-sm"
        />
      </label>
      <Button
        operation="v1_csv_destination"
        disabled={!valid || busy || !/^[a-z][a-z0-9_]{0,62}$/.test(table)}
        onClick={() =>
          void action(() =>
            prepareUploadDestination({
              data: {
                env,
                upload_id: upload.upload_id,
                expected_version: upload.version,
                table,
              },
            }),
          )
        }
      >
        {busy ? 'Preparing…' : 'Prepare candidate destination'}
      </Button>
      {!valid ? (
        <p className="text-sm text-muted-foreground">
          Save a valid column review before preparing a destination.
        </p>
      ) : null}
    </div>
  )
}

function BaseReference({ receipt }: { receipt: UploadCandidate }) {
  return (
    <p className="text-sm text-muted-foreground">
      Base reference {receipt.base_ref}{' '}
      {receipt.main_unchanged === true
        ? 'is unchanged from preparation.'
        : receipt.main_unchanged === false
          ? 'changed independently since preparation; no merge was performed here.'
          : 'has not been checked for changes since preparation.'}{' '}
      The candidate remains separate.
    </p>
  )
}

function CandidateProgress({
  receipt,
  env,
  upload,
  busy,
  valid,
  action,
}: {
  receipt: UploadCandidate
  env: string
  upload: CsvUpload
  busy: boolean
  valid: boolean
  action: (work: () => Promise<UploadCandidate>) => Promise<void>
}) {
  const waiting = receipt.status === 'queued' || receipt.status === 'running'
  return (
    <div className="space-y-4">
      <dl className="grid gap-4 text-sm sm:grid-cols-2">
        <div className="space-y-1">
          <dt className="text-muted-foreground">Destination</dt>
          <dd className="break-all font-mono">{receipt.relation}</dd>
        </div>
        <div className="space-y-1">
          <dt className="text-muted-foreground">Based on</dt>
          <dd className="break-all font-mono">{receipt.base_ref}</dd>
        </div>
      </dl>
      {waiting ? (
        <p role="status" className="max-w-prose text-sm text-muted-foreground">
          {receipt.status === 'queued'
            ? 'The load has been submitted.'
            : 'The load is running.'}{' '}
          Check its status to read the candidate rows after the run succeeds.
        </p>
      ) : receipt.status === 'failed' ? (
        <p className="max-w-prose text-sm text-bad-ink">
          The previous run failed. Prepare a new verified plan, then confirm the
          retry. No retry starts automatically.
        </p>
      ) : (
        <p className="max-w-prose text-sm text-muted-foreground">
          {receipt.plan_hash
            ? 'The load plan is ready for your confirmation.'
            : 'The destination is prepared. Estimate the one-off load before confirming it.'}{' '}
          The load writes only to this separate candidate.
        </p>
      )}
      <CandidateActions
        receipt={receipt}
        env={env}
        upload={upload}
        busy={busy}
        valid={valid}
        action={action}
      />
      {receipt.status === 'planned' && receipt.plan_hash ? (
        <p className="text-sm">
          One unpartitioned asset
          {upload.row_count !== null
            ? ` · ${upload.row_count.toLocaleString()} reviewed source rows`
            : ''}
        </p>
      ) : null}
      {!valid && receipt.status === 'planned' ? (
        <p className="text-sm text-muted-foreground">
          Save a valid column review before confirming the load.
        </p>
      ) : null}
    </div>
  )
}

function CandidateActions({
  receipt,
  env,
  upload,
  busy,
  valid,
  action,
}: {
  receipt: UploadCandidate
  env: string
  upload: CsvUpload
  busy: boolean
  valid: boolean
  action: (work: () => Promise<UploadCandidate>) => Promise<void>
}) {
  const identity = { env, upload_id: upload.upload_id }
  return (
    <>
      {' '}
      {['prepared', 'planned', 'failed'].includes(receipt.status) ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            operation="v1_csv_candidate_plan"
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
          {receipt.status === 'planned' && receipt.plan_hash ? (
            <Button
              operation="v1_csv_candidate_load"
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
        </div>
      ) : null}
    </>
  )
}

function CandidateEvidence({
  receipt,
  preview,
}: {
  receipt: UploadCandidate
  preview: UploadCandidatePreview | null
}) {
  const fields = [
    ['Candidate ref', receipt.candidate_ref],
    ['Candidate hash', receipt.candidate_hash],
    ['Base hash at preparation', receipt.base_hash],
    ['Run ID', receipt.run_id],
    ['Snapshot ID', receipt.snapshot_id],
    ['Query ID', preview?.query_id],
    ['Plan hash', receipt.plan_hash],
    ['Job', receipt.job_name],
    ['Job snapshot ID', receipt.job_snapshot_id],
    ['Launch ref hash', receipt.launch_ref_hash],
    ['Asset', receipt.asset_id],
    ['Review version', receipt.review_version],
    ['Recipe digest', receipt.recipe_digest],
    ['Source checksum', receipt.checksum],
    ['Contract digest', receipt.contract_digest],
    ['Source digest', receipt.source_digest],
    ['Reviewed by', receipt.actor],
  ]
  return (
    <div className="space-y-3 border-t border-line pt-4 text-xs">
      <details>
        <summary className="w-fit cursor-pointer rounded text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
          Evidence
        </summary>
        <dl className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-2">
          {fields
            .filter(([, value]) => value !== null && value !== undefined)
            .map(([label, value]) => (
              <div key={label} className="min-w-0 space-y-1">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="break-all font-mono">{value}</dd>
              </div>
            ))}
        </dl>
      </details>
      {receipt.attempts.length ? (
        <details>
          <summary className="w-fit cursor-pointer rounded text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
            Prior attempts ({receipt.attempts.length})
          </summary>
          <ul className="mt-4 space-y-4">
            {receipt.attempts.map((attempt) => (
              <li key={attempt.run_id} className="space-y-2">
                <p className="font-medium">{attempt.status}</p>
                <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                  {Object.entries(attempt)
                    .filter(([name]) => name !== 'status')
                    .map(([name, value]) => (
                      <div key={name} className="min-w-0 space-y-1">
                        <dt className="text-muted-foreground">
                          {name.replaceAll('_', ' ')}
                        </dt>
                        <dd className="break-all font-mono">{value}</dd>
                      </div>
                    ))}
                </dl>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  )
}

function CandidatePreview({
  preview,
  upload,
}: {
  preview: UploadCandidatePreview
  upload: CsvUpload
}) {
  const [showTechnical, setShowTechnical] = React.useState(false)
  const reviewedNames = new Set(
    upload.output_columns?.length
      ? upload.output_columns.map((column) => column.name)
      : upload.columns.flatMap((column) => [
          column.name,
          ...(column.qualifiers
            ? [column.qualifiers.value_name, column.qualifiers.flag_name]
            : []),
        ]),
  )
  const userColumns = preview.columns.filter((name) => reviewedNames.has(name))
  const columns = showTechnical
    ? [
        ...userColumns,
        ...preview.columns.filter((name) => !reviewedNames.has(name)),
      ]
    : userColumns
  const technicalCount = preview.columns.length - userColumns.length
  return (
    <div className="min-w-0 max-w-full space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Loaded data</h3>
        {technicalCount > 0 ? (
          <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={showTechnical}
              onChange={(event) => setShowTechnical(event.target.checked)}
              className="size-3.5 accent-primary"
            />
            Show technical columns ({technicalCount})
          </label>
        ) : null}
      </div>
      {columns.length ? (
        <div className="min-w-0 max-w-full overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Candidate table preview</caption>
            <thead className="bg-raised">
              <tr>
                {columns.map((name) => (
                  <th
                    key={name}
                    scope="col"
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
                  {columns.map((name) => (
                    <td
                      key={name}
                      className="max-w-64 truncate border-b border-line px-3 py-2 tabular-nums"
                      title={row[name] === null ? 'NULL' : String(row[name])}
                    >
                      {row[name] === null ? (
                        <span className="text-muted-foreground">NULL</span>
                      ) : (
                        String(row[name])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          No reviewed user columns are available in this preview. Show technical
          columns to inspect the returned fields.
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Showing {preview.rows.length.toLocaleString()} of{' '}
        {preview.row_count.toLocaleString()} actual rows from the pinned
        candidate table.
      </p>
    </div>
  )
}
