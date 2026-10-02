/** Collects an asset materialization request and submits it for execution. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import type { Env } from '@/lib/data/types'
import { materializeAsset } from '@/lib/data/api/assets'
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
import { Select } from '@/components/ui/select'
import { Mono } from '@/components/phlo/status'

type State =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'failed'; message: string }
  | { kind: 'accepted'; runId: string; ref: string }

export function MaterializeDialog({
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
  const [confirmed, setConfirmed] = React.useState(false)
  const [state, setState] = React.useState<State>({ kind: 'idle' })
  const key = React.useRef<string | null>(null)
  const submitting = React.useRef(false)
  const storageKey = `phlo:materialize:${env}:${assetId}:${job}`
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting.current || state.kind === 'accepted' || !confirmed || !job)
      return
    submitting.current = true
    setState({ kind: 'pending' })
    try {
      key.current ??= sessionStorage.getItem(storageKey) ?? crypto.randomUUID()
      sessionStorage.setItem(storageKey, key.current)
      const result = await materializeAsset({
        data: {
          env,
          id: assetId,
          job_name: job,
          idempotency_key: key.current,
          confirmed: true,
        },
      })
      setState({
        kind: 'accepted',
        runId: result.run_id,
        ref: result.nessie_ref,
      })
    } catch (error) {
      setState({
        kind: 'failed',
        message:
          error instanceof Error
            ? error.message
            : 'Materialization request failed.',
      })
    } finally {
      submitting.current = false
    }
  }
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
          <DialogHeader>
            <DialogTitle>Materialize</DialogTitle>
            <DialogDescription>
              <Mono>{assetId}</Mono> · {env}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Field>
              <FieldLabel>Job</FieldLabel>
              {key.current !== null ? (
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
              <FieldDescription>
                Uses the job's configured Nessie reference.
              </FieldDescription>
            </Field>
            <p className="m-0 text-[12.5px] leading-snug text-muted-foreground">
              This submits a real Dagster run in {env}, using its configured
              Nessie reference. Partition backfills and cost estimates are not
              connected in this dialog.
            </p>
            <CheckLine
              checked={confirmed}
              disabled={state.kind === 'pending' || state.kind === 'accepted'}
              onCheckedChange={setConfirmed}
            >
              I confirm this materialization in {env}.
            </CheckLine>
            {state.kind === 'failed' ? (
              <div role="alert" className="text-sm text-bad-text">
                {state.message} Retrying here or after a reload reuses the same
                operation key. Check the run history if the response was lost.
              </div>
            ) : null}
            {state.kind === 'accepted' ? (
              <div role="status" className="flex flex-col gap-2 text-sm">
                Dagster accepted run {state.runId} on ref {state.ref}. This is
                not a success result.
                <Link
                  to="/pipelines/$jobName"
                  params={{ jobName: job }}
                  search={{ env, run: state.runId }}
                >
                  View run
                </Link>
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
                  New materialization
                </Button>
              </div>
            ) : null}
          </DialogBody>
          <DialogFooter className="flex-wrap">
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
                !confirmed ||
                !job ||
                state.kind === 'pending' ||
                state.kind === 'accepted'
              }
            >
              {state.kind === 'pending'
                ? 'Submitting…'
                : state.kind === 'failed'
                  ? 'Retry request'
                  : 'Start materialization'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
