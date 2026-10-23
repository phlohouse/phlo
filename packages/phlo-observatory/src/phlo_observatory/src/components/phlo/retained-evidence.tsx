/** Retained evidence can be archived or reconciled, never silently promoted. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import type {
  CandidateEvidence,
  SourceHandoff,
} from '@/lib/data/api/candidate-evidence'
import { ActionButton as Button } from '@/components/phlo/action-button'
import { ActionGroup } from '@/components/ui/action-group'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export function EvidenceStatus({
  evidence,
}: {
  evidence?: CandidateEvidence | null
}) {
  if (!evidence)
    return (
      <p className="m-0 text-xs text-muted-foreground">
        Current release evidence has not been verified.
      </p>
    )
  return (
    <div className="grid gap-2 text-xs">
      <p
        role="status"
        className={
          evidence.stale ? 'm-0 text-warn-text' : 'm-0 text-muted-foreground'
        }
      >
        {evidence.stale
          ? 'Historical evidence · reference drift detected. Recorded results are not current release approval.'
          : evidence.eligible_for_release
            ? 'Candidate evidence verified. Source review and signed release are still required.'
            : 'Candidate is not eligible for release.'}{' '}
        {evidence.reason}
      </p>
      <details>
        <summary className="cursor-pointer">Pinned reference evidence</summary>
        <dl className="mt-2 grid gap-2 break-all font-mono">
          {Object.entries(evidence)
            .filter(([key]) => key.endsWith('hash'))
            .map(([key, value]) => (
              <div key={key}>
                <dt className="text-muted-foreground">
                  {key.replaceAll('_', ' ')}
                </dt>
                <dd>{value ?? 'Not recorded'}</dd>
              </div>
            ))}
        </dl>
      </details>
    </div>
  )
}

export function ArchiveEvidenceAction({
  archived,
  reason,
  operation,
  disabled,
  onChange,
}: {
  archived: boolean
  reason?: string | null
  operation: string
  disabled?: boolean
  onChange: (input: { archived: boolean; reason: string }) => Promise<void>
}) {
  const [open, setOpen] = React.useState(false)
  const [note, setNote] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  async function apply() {
    setBusy(true)
    setError('')
    try {
      await onChange({ archived: !archived, reason: note.trim() })
      setOpen(false)
      setNote('')
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'The archive state was not changed.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Button
        variant="outline"
        operation={operation}
        disabled={disabled}
        onClick={() => {
          setError('')
          setOpen(true)
        }}
      >
        {archived ? 'Restore evidence' : 'Archive evidence'}
      </Button>
      <Dialog open={open} onOpenChange={(value) => !busy && setOpen(value)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {archived
                ? 'Restore retained evidence'
                : 'Archive retained evidence'}
            </DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm">
              {archived
                ? 'Return this record to the active inventory.'
                : 'Hide this record from the active inventory without deleting its bytes, source or receipts.'}{' '}
              This does not retry an execution, release mutation fences or
              approve a release.
            </p>
            {reason ? (
              <p className="text-xs text-muted-foreground">
                Previous archive reason: {reason}
              </p>
            ) : null}
            <label className="grid gap-2 text-sm">
              Reason
              <textarea
                value={note}
                maxLength={1000}
                onChange={(event) => setNote(event.target.value)}
                className="min-h-24 rounded-md border border-line bg-background p-3"
              />
            </label>
            {error ? (
              <p role="alert" className="text-sm text-bad-text">
                {error}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              operation={operation}
              disabled={busy || !note.trim()}
              onClick={() => void apply()}
            >
              {busy
                ? 'Applying…'
                : archived
                  ? 'Confirm restore'
                  : 'Confirm archive'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function SourceHandoffPanel({
  operation,
  load,
  disabled,
}: {
  operation: string
  load: () => Promise<SourceHandoff>
  disabled?: boolean
}) {
  const [handoff, setHandoff] = React.useState<SourceHandoff>()
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  async function read() {
    setBusy(true)
    setError('')
    setHandoff(undefined)
    try {
      setHandoff(await load())
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Source handoff is unavailable.',
      )
    } finally {
      setBusy(false)
    }
  }
  function download(value: SourceHandoff) {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }),
    )
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `phlo-${value.kind}-source-review.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }
  return (
    <section
      aria-label="Source review handoff"
      className="grid min-w-0 gap-3 rounded-lg border border-line p-3 text-xs"
    >
      <ActionGroup>
        <Button
          operation={operation}
          variant="outline"
          disabled={busy || disabled}
          onClick={() => void read()}
        >
          {busy
            ? 'Reading source…'
            : handoff
              ? 'Refresh source handoff'
              : 'Prepare source handoff'}
        </Button>
      </ActionGroup>
      <p className="m-0 text-muted-foreground">
        Review source files in your project before release. Export does not
        publish, apply source changes or grant access.
      </p>
      {error ? (
        <p role="alert" className="m-0 text-bad-text">
          {error}
        </p>
      ) : null}
      {handoff ? (
        <>
          <EvidenceStatus evidence={handoff.candidate_evidence} />
          <ul className="m-0 grid min-w-0 list-none gap-2 p-0">
            {handoff.files.map((file) => (
              <li key={file.path}>
                <details>
                  <summary className="cursor-pointer break-all font-mono">
                    {file.path}
                  </summary>
                  <p className="break-all text-muted-foreground">
                    SHA-256 {file.sha256}
                  </p>
                  <pre
                    role="region"
                    aria-label={`Source file ${file.path}`}
                    tabIndex={0}
                    className="max-h-64 max-w-full overflow-auto rounded bg-background p-3"
                  >
                    {file.content}
                  </pre>
                </details>
              </li>
            ))}
          </ul>
          <p className="m-0 text-muted-foreground">
            Release target:{' '}
            <span className="break-all font-mono">
              {handoff.release.target_ref}
            </span>
            . A fresh signature is required; candidate evidence is not release
            approval.
          </p>
          {handoff.release.blocked_reason ? (
            <p role="status" className="m-0 text-warn-text">
              {handoff.release.blocked_reason}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={() => download(handoff)}>
              Download review bundle
            </Button>
            {handoff.release.source_ref ? (
              <Link
                to="/branches"
                search={{
                  env: handoff.env,
                  branch: handoff.release.source_ref,
                }}
                className="text-link"
              >
                Review branch checks and signed release
              </Link>
            ) : null}
          </div>
        </>
      ) : null}
    </section>
  )
}
