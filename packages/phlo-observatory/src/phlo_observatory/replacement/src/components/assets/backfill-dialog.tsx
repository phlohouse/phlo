/** Collects and validates an asset backfill request. */
import * as React from 'react'
import { z } from 'zod'
import type { Env } from '@/lib/data/types'
import { backfillAsset } from '@/lib/data/api/assets'
import { Mono } from '@/components/phlo/status'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

type State =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'failed'; message: string }
  | { kind: 'accepted'; ref: string; evidence: string }

function backfillRequestValid(
  confirmed: boolean,
  job: string,
  partitionSet: string,
  selection: 'explicit' | 'latest' | 'all',
  partitions: Array<string>,
) {
  return (
    confirmed &&
    Boolean(job) &&
    Boolean(partitionSet) &&
    (selection !== 'explicit' || partitions.length > 0)
  )
}

export function BackfillDialog({
  open,
  onClose,
  assetId,
  env,
  jobs,
}: {
  open: boolean
  onClose: () => void
  assetId: string
  env: Env
  jobs: Array<string>
}) {
  const [job, setJob] = React.useState(jobs[0] ?? '')
  const [partitionSet, setPartitionSet] = React.useState('')
  const [selection, setSelection] = React.useState<
    'explicit' | 'latest' | 'all'
  >('explicit')
  const [partitionText, setPartitionText] = React.useState('')
  const [confirmed, setConfirmed] = React.useState(false)
  const [state, setState] = React.useState<State>({ kind: 'idle' })
  const key = React.useRef<string | null>(null)
  const submitting = React.useRef(false)
  const intent = `${job}:${partitionSet}:${selection}:${partitionText}`
  const storageKey = `phlo:backfill:${env}:${assetId}:${intent}`
  const partitions = partitionText
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter(Boolean)
  const requestValid = backfillRequestValid(
    confirmed,
    job,
    partitionSet,
    selection,
    partitions,
  )
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting.current || state.kind === 'accepted' || !requestValid) return
    submitting.current = true
    setState({ kind: 'pending' })
    try {
      key.current ??= sessionStorage.getItem(storageKey) ?? crypto.randomUUID()
      sessionStorage.setItem(storageKey, key.current)
      const response = await backfillAsset({
        data: {
          env,
          id: assetId,
          job_name: job,
          partition_set_name: partitionSet,
          selection,
          partitions,
          idempotency_key: key.current,
          confirmed: true,
        },
      })
      setState({
        kind: 'accepted',
        ref: response.nessie_ref,
        evidence: JSON.stringify(response.result, null, 2),
      })
    } catch (error) {
      setState({
        kind: 'failed',
        message:
          error instanceof Error ? error.message : 'Backfill request failed.',
      })
    } finally {
      submitting.current = false
    }
  }
  const locked =
    key.current !== null ||
    state.kind === 'pending' ||
    state.kind === 'accepted'
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value && state.kind !== 'pending') onClose()
      }}
    >
      <DialogContent>
        <form
          onSubmit={(event) => void submit(event)}
          className="flex min-h-0 flex-col"
        >
          <DialogHeader className="shrink-0">
            <DialogTitle>Backfill partitions</DialogTitle>
            <DialogDescription>
              <Mono>{assetId}</Mono> · pinned to {env}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div
              className={
                state.kind === 'accepted'
                  ? 'hidden'
                  : 'flex flex-col gap-[18px]'
              }
            >
              <label className="flex flex-col gap-2 text-sm">
                Job
                <select
                  value={job}
                  disabled={locked}
                  onChange={(e) => setJob(e.target.value)}
                  className="h-10 rounded-lg border border-border bg-card px-3"
                >
                  {jobs.map((name) => (
                    <option key={name}>{name}</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-2 text-sm">
                Partition set
                <Input
                  required
                  value={partitionSet}
                  disabled={locked}
                  onChange={(e) => setPartitionSet(e.target.value)}
                  placeholder="orders_daily"
                />
              </label>
              <label className="flex flex-col gap-2 text-sm">
                Selection
                <select
                  value={selection}
                  disabled={locked}
                  onChange={(e) =>
                    setSelection(
                      z
                        .enum(['explicit', 'latest', 'all'])
                        .parse(e.target.value),
                    )
                  }
                  className="h-10 rounded-lg border border-border bg-card px-3"
                >
                  <option value="explicit">Explicit keys</option>
                  <option value="latest">Latest partition</option>
                  <option value="all">All partitions</option>
                </select>
              </label>
              {selection === 'explicit' ? (
                <label className="flex flex-col gap-2 text-sm">
                  Partition keys
                  <textarea
                    required
                    value={partitionText}
                    disabled={locked}
                    onChange={(e) => setPartitionText(e.target.value)}
                    rows={4}
                    placeholder="One key per line or comma-separated"
                    className="rounded-lg border border-border bg-card p-3 font-mono text-sm"
                  />
                </label>
              ) : null}
              <p className="m-0 text-sm text-muted-foreground">
                Cost, bytes, duration, and workload estimates are unavailable.
                The API validates the partition set before submitting.
              </p>
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={confirmed}
                  disabled={locked}
                  onChange={(e) => setConfirmed(e.target.checked)}
                  className="mt-1"
                />
                I confirm this real backfill in {env}.
              </label>
            </div>
            {state.kind === 'failed' ? (
              <div role="alert" className="text-sm text-bad-text">
                {state.message} Retry reuses the same operation key; inspect run
                history if the response was lost.
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    key.current = null
                    setConfirmed(false)
                    setState({ kind: 'idle' })
                  }}
                >
                  Edit request
                </Button>
              </div>
            ) : null}
            {state.kind === 'accepted' ? (
              <div role="status" className="flex flex-col gap-2 text-sm">
                <p className="m-0 break-all">
                  {job} ·{' '}
                  {selection === 'explicit'
                    ? partitions.join(', ')
                    : `${selection} partitions`}{' '}
                  · {env}
                </p>
                API response on ref {state.ref}. Acceptance is not completion.
                <pre className="shrink-0 overflow-auto rounded-lg bg-sunken p-3 text-xs">
                  {state.evidence}
                </pre>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    sessionStorage.removeItem(storageKey)
                    key.current = null
                    setConfirmed(false)
                    setState({ kind: 'idle' })
                  }}
                >
                  New backfill
                </Button>
              </div>
            ) : null}
          </DialogBody>
          <DialogFooter className="shrink-0">
            <Button
              type="button"
              variant="outline"
              disabled={state.kind === 'pending'}
              onClick={onClose}
              className="ml-auto"
            >
              Close
            </Button>
            <Button
              type="submit"
              disabled={
                !requestValid ||
                state.kind === 'pending' ||
                state.kind === 'accepted'
              }
            >
              {state.kind === 'pending'
                ? 'Submitting…'
                : state.kind === 'failed'
                  ? 'Retry request'
                  : 'Start backfill'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
