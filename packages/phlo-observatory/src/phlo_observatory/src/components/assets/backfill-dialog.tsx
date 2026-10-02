/** Collects and validates an asset backfill request. */
import * as React from 'react'
import type { Env } from '@/lib/data/types'
import { backfillAsset } from '@/lib/data/api/assets'
import { Mono } from '@/components/phlo/status'
import { Button } from '@/components/ui/button'
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
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input, Textarea } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Segmented } from '@/components/ui/toggle-group'

type State =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'failed'; message: string }
  | { kind: 'accepted'; ref: string; evidence: string }

type Selection = 'explicit' | 'latest' | 'all'

function backfillRequestValid(
  confirmed: boolean,
  job: string,
  partitionSet: string,
  selection: Selection,
  partitions: Array<string>,
) {
  return (
    confirmed &&
    Boolean(job) &&
    Boolean(partitionSet) &&
    (selection !== 'explicit' || partitions.length > 0)
  )
}

function BackfillRequestFields({
  job,
  setJob,
  jobs,
  partitionSet,
  setPartitionSet,
  selection,
  setSelection,
  partitionText,
  setPartitionText,
  confirmed,
  setConfirmed,
  locked,
  hidden,
  env,
}: {
  job: string
  setJob: (job: string) => void
  jobs: Array<string>
  partitionSet: string
  setPartitionSet: (partitionSet: string) => void
  selection: Selection
  setSelection: (selection: Selection) => void
  partitionText: string
  setPartitionText: (partitionText: string) => void
  confirmed: boolean
  setConfirmed: (confirmed: boolean) => void
  locked: boolean
  hidden: boolean
  env: Env
}) {
  return (
    <div className={hidden ? 'hidden' : 'flex flex-col gap-[18px]'}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field>
          <FieldLabel>Job</FieldLabel>
          {locked ? (
            <div className="flex h-9 items-center rounded-lg border border-input bg-card px-3 opacity-50">
              <Mono>{job}</Mono>
            </div>
          ) : (
            <Select
              value={job}
              onValueChange={setJob}
              className="font-mono text-[13px]"
              options={jobs.map((name) => ({ value: name, label: name }))}
            />
          )}
        </Field>
        <Field>
          <FieldLabel>Partition set</FieldLabel>
          <Input
            required
            value={partitionSet}
            disabled={locked}
            onChange={(e) => setPartitionSet(e.target.value)}
            placeholder="orders_daily"
            className="font-mono text-[13px]"
          />
        </Field>
      </div>
      <Field>
        <FieldLabel>Selection</FieldLabel>
        {locked ? (
          <div className="text-[13.5px] text-muted-foreground">
            {selection === 'explicit'
              ? 'Explicit keys'
              : selection === 'latest'
                ? 'Latest partition'
                : 'All partitions'}
          </div>
        ) : (
          <Segmented
            value={selection}
            onValueChange={setSelection}
            className="self-start"
            options={[
              { value: 'explicit', label: 'Explicit keys' },
              { value: 'latest', label: 'Latest partition' },
              { value: 'all', label: 'All partitions' },
            ]}
          />
        )}
      </Field>
      {selection === 'explicit' ? (
        <Field>
          <FieldLabel>Partition keys</FieldLabel>
          <Textarea
            required
            value={partitionText}
            disabled={locked}
            onChange={(e) => setPartitionText(e.target.value)}
            rows={4}
            placeholder="One key per line or comma-separated"
            className="font-mono text-[13px]"
          />
          <FieldDescription>
            One key per line or comma-separated.
          </FieldDescription>
        </Field>
      ) : null}
      <p className="m-0 text-[12.5px] leading-snug text-muted-foreground">
        Cost, bytes, duration, and workload estimates are unavailable. The API
        validates the partition set before submitting.
      </p>
      <CheckLine
        checked={confirmed}
        disabled={locked}
        onCheckedChange={setConfirmed}
      >
        I confirm this real backfill in {env}.
      </CheckLine>
    </div>
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
  const [selection, setSelection] = React.useState<Selection>('explicit')
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
            <BackfillRequestFields
              job={job}
              setJob={setJob}
              jobs={jobs}
              partitionSet={partitionSet}
              setPartitionSet={setPartitionSet}
              selection={selection}
              setSelection={setSelection}
              partitionText={partitionText}
              setPartitionText={setPartitionText}
              confirmed={confirmed}
              setConfirmed={setConfirmed}
              locked={locked}
              hidden={state.kind === 'accepted'}
              env={env}
            />
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
          <DialogFooter className="shrink-0 flex-wrap">
            <Button
              type="button"
              variant="outline"
              disabled={state.kind === 'pending'}
              onClick={onClose}
              size="lg"
              className="ml-auto h-10 bg-card sm:h-9"
            >
              Close
            </Button>
            <Button
              type="submit"
              size="lg"
              className="h-10 sm:h-9"
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
