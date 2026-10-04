/** Confirms pipeline operations and preserves idempotent retries. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import type { Env } from '@/lib/data/types'
import { retryRun } from '@/lib/data/api/pipelines'
import { Button } from '@/components/ui/button'

type ActionState =
  | { kind: 'idle' | 'pending' | 'accepted' }
  | { kind: 'failed'; message: string }

export function ConfirmedAction({
  storageKey,
  confirmation,
  actionLabel,
  acceptedMessage,
  execute,
}: {
  storageKey: string
  confirmation: string
  actionLabel: string
  acceptedMessage: string
  execute: (idempotencyKey: string) => Promise<void>
}) {
  const [confirmed, setConfirmed] = React.useState(false)
  const [state, setState] = React.useState<ActionState>({ kind: 'idle' })
  const submitting = React.useRef(false)

  async function submit() {
    if (!confirmed || submitting.current || state.kind === 'accepted') return
    submitting.current = true
    setState({ kind: 'pending' })
    const idempotencyKey =
      sessionStorage.getItem(storageKey) ?? crypto.randomUUID()
    sessionStorage.setItem(storageKey, idempotencyKey)
    try {
      await execute(idempotencyKey)
      sessionStorage.removeItem(storageKey)
      setState({ kind: 'accepted' })
    } catch (error) {
      setState({
        kind: 'failed',
        message:
          error instanceof Error ? error.message : 'Action request failed.',
      })
    } finally {
      submitting.current = false
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={state.kind === 'pending' || state.kind === 'accepted'}
          onChange={(event) => setConfirmed(event.target.checked)}
          className="mt-1"
        />
        {confirmation}
      </label>
      <Button
        disabled={
          !confirmed || state.kind === 'pending' || state.kind === 'accepted'
        }
        onClick={() => void submit()}
      >
        {state.kind === 'pending'
          ? 'Submitting…'
          : state.kind === 'failed'
            ? `Retry ${actionLabel.toLowerCase()}`
            : actionLabel}
      </Button>
      {state.kind === 'failed' ? (
        <p role="alert" className="m-0 text-sm text-bad-text">
          {state.message} Retrying reuses the same operation key. Check current
          state if the response was lost.
        </p>
      ) : null}
      {state.kind === 'accepted' ? (
        <p role="status" className="m-0 text-sm">
          {acceptedMessage}
        </p>
      ) : null}
    </div>
  )
}

type RetryState =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'failed'; message: string }
  | { kind: 'accepted'; runId: string }

export function RetryControl({
  env,
  runId,
  jobId,
}: {
  env: Env
  runId: string
  jobId: string
}) {
  const [confirmed, setConfirmed] = React.useState(false)
  const [state, setState] = React.useState<RetryState>({ kind: 'idle' })
  const key = React.useRef<string | null>(null)
  const submitting = React.useRef(false)
  const storageKey = `phlo:retry:${env}:${runId}`
  async function retry() {
    if (!confirmed || submitting.current || state.kind === 'accepted') return
    submitting.current = true
    setState({ kind: 'pending' })
    try {
      key.current ??= sessionStorage.getItem(storageKey) ?? crypto.randomUUID()
      sessionStorage.setItem(storageKey, key.current)
      const result = await retryRun({
        data: {
          env,
          run_id: runId,
          idempotency_key: key.current,
          confirmed: true,
        },
      })
      setState({ kind: 'accepted', runId: result.run_id })
    } catch (error) {
      setState({
        kind: 'failed',
        message:
          error instanceof Error ? error.message : 'Retry request failed.',
      })
    } finally {
      submitting.current = false
    }
  }
  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={state.kind === 'pending' || state.kind === 'accepted'}
          onChange={(event) => setConfirmed(event.target.checked)}
          className="mt-1"
        />
        I confirm a retry from failure in {env}.
      </label>
      <Button
        disabled={
          !confirmed || state.kind === 'pending' || state.kind === 'accepted'
        }
        onClick={() => void retry()}
      >
        {state.kind === 'pending'
          ? 'Submitting…'
          : state.kind === 'failed'
            ? 'Retry request'
            : 'Re-run from failed step'}
      </Button>
      {state.kind === 'failed' ? (
        <p role="alert" className="m-0 text-sm text-bad-text">
          {state.message} Retrying here or after a reload reuses the same
          operation key. Check the run history if the response was lost.
        </p>
      ) : null}
      {state.kind === 'accepted' ? (
        <p role="status" className="m-0 text-sm">
          Dagster accepted a retry. Completion is not yet known.{' '}
          <Link
            to="/pipelines/$jobName/runs/$runId"
            params={{ jobName: jobId, runId: state.runId }}
            search={{ env }}
            className="inline-block"
          >
            View retry run
          </Link>
        </p>
      ) : null}
    </div>
  )
}
