/** Defines pipeline details, run events, and guarded run operations. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { z } from 'zod'
import type { Env } from '@/lib/data/types'
import {
  cancelRun,
  changeSchedule,
  getPipelineJob,
  launchJob,
  retryRun,
} from '@/lib/data/api/pipelines'
import { Eyebrow, KeyValues, PageHeader } from '@/components/phlo/page'
import { Mono } from '@/components/phlo/status'
import { EmptyState } from '@/components/phlo/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { runColor } from '@/components/pipelines/bits'

export const Route = createFileRoute('/_app/pipelines/$jobName')({
  validateSearch: z.object({ run: z.string().min(1).optional() }),
  loaderDeps: ({ search }) => ({ env: search.env, run: search.run }),
  loader: ({ params, deps }) =>
    getPipelineJob({ data: { id: params.jobName, ...deps } }),
  head: ({ params }) => ({ meta: [{ title: `${params.jobName} · phlo` }] }),
  component: PipelinePage,
})

type PipelineData = Awaited<ReturnType<typeof getPipelineJob>>

const statusBadge = (status: PipelineData['runs'][number]['status']) =>
  status === 'FAILURE'
    ? 'bad'
    : status === 'SUCCESS'
      ? 'ok'
      : status === 'CANCELED'
        ? 'neutral'
        : 'warn'

function duration(seconds: number | null) {
  if (seconds === null) return 'Not available'
  if (seconds < 60) return `${seconds.toFixed(2)} seconds`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

function RunEvents({ events }: { events: PipelineData['events'] }) {
  if (!events?.items.length)
    return (
      <p className="m-0 text-sm text-muted-foreground">No events observed.</p>
    )
  return (
    <ul className="m-0 flex list-none flex-col gap-2 p-0 text-xs">
      {events.items
        .filter((event) => event.event_type !== 'LOG_MESSAGE')
        .map((event, index) => (
          <li key={index} className="border-b border-line-soft pb-2">
            <div className="font-mono">
              {event.step_key ?? 'Run'} · {event.event_type}
            </div>
            <time className="text-muted-foreground">{event.timestamp}</time>
          </li>
        ))}
    </ul>
  )
}

function SelectedRun({ data }: { data: PipelineData }) {
  const { selected, events, env, job } = data
  const [logsOpen, setLogsOpen] = React.useState(false)
  if (!selected)
    return (
      <aside
        aria-label="Selected run"
        className="flex shrink-0 flex-col gap-4 border-t border-line px-4 py-5 lg:w-[440px] lg:overflow-y-auto lg:border-t-0 lg:px-6"
      >
        <EmptyState title="No run observed" />
      </aside>
    )
  return (
    <aside
      aria-label="Selected run"
      className="flex shrink-0 flex-col border-t border-line lg:w-[440px] lg:overflow-y-auto lg:border-t-0"
    >
      <div className="flex flex-col gap-2.5 border-b border-line px-4 pt-5 pb-4 lg:px-6 lg:pt-[22px] lg:pb-[18px]">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={statusBadge(selected.status)} className="font-medium">
            {selected.status.replace('_', ' ')}
          </Badge>
          <span className="text-[13px] text-muted-foreground">
            Run <Mono className="text-[12.5px]">{selected.run_id}</Mono>
            {data.runs[0]?.run_id === selected.run_id ? ' · latest' : ''}
          </span>
        </div>
        <div className="break-all font-mono text-lg font-medium">{job.id}</div>
        <KeyValues
          className="mt-1 text-[13.5px]"
          items={[
            ['Duration', duration(selected.duration_seconds)],
            ['Created', selected.created_at],
            ['Started', selected.started_at ?? 'Not started'],
            ['Ended', selected.ended_at ?? 'Not ended'],
            ['Trigger', 'Unavailable from API'],
          ]}
        />
      </div>
      <div className="flex flex-col gap-3 border-b border-line px-4 py-[18px] lg:px-6">
        <Eyebrow>Run events</Eyebrow>
        <RunEvents events={events} />
        {events?.truncated ? (
          <p className="m-0 text-xs text-muted-foreground">
            Only the first 100 events are shown. More events are available from
            the API.
          </p>
        ) : null}
      </div>
      {logsOpen ? (
        <div className="flex flex-col gap-2 px-4 py-[18px] lg:px-6">
          <Eyebrow>Logs</Eyebrow>
          <pre className="m-0 max-h-80 overflow-auto rounded-lg border border-line bg-sunken p-3 font-mono text-xs whitespace-pre-wrap">
            {events?.items
              .map(
                (event) =>
                  `${event.timestamp} ${event.event_type} ${event.step_key ?? ''}\n${event.message}`,
              )
              .join('\n\n') || 'No log evidence returned.'}
          </pre>
        </div>
      ) : null}
      <div className="mt-auto flex flex-col gap-3 border-t border-line px-4 py-4 lg:px-6">
        <Button
          variant="outline"
          aria-expanded={logsOpen}
          onClick={() => setLogsOpen((value) => !value)}
        >
          {logsOpen ? 'Hide logs' : 'View logs'}
        </Button>
        {selected.status === 'FAILURE' ? (
          <RetryControl
            key={`${env}:${selected.run_id}`}
            env={env}
            runId={selected.run_id}
            jobId={job.id}
          />
        ) : null}
        {selected.status === 'STARTED' ? (
          <ConfirmedAction
            key={`cancel:${env}:${selected.run_id}`}
            storageKey={`phlo:cancel:${env}:${selected.run_id}:STARTED`}
            confirmation={`I confirm canceling run ${selected.run_id} in ${env}.`}
            actionLabel="Cancel run"
            acceptedMessage="Dagster accepted the cancellation. Completion is not yet known."
            execute={async (idempotencyKey) => {
              await cancelRun({
                data: {
                  env,
                  run_id: selected.run_id,
                  idempotency_key: idempotencyKey,
                  confirmed: true,
                },
              })
            }}
          />
        ) : null}
      </div>
    </aside>
  )
}

function PipelinePage() {
  const data = Route.useLoaderData()
  const { job, runs, schedules, selected, env } = data
  const navigate = Route.useNavigate()
  const router = useRouter()
  const [partitionKey, setPartitionKey] = React.useState('')
  const [controlsOpen, setControlsOpen] = React.useState(false)
  return (
    <>
      <PageHeader
        crumbs={[{ label: 'Pipelines', to: '/pipelines' }]}
        title={<Mono className="text-[13.5px]">{job.id}</Mono>}
        meta={`${job.repository_name} · ${env}`}
        actions={
          <>
            <Button variant="outline" onClick={() => setControlsOpen(true)}>
              Job controls
            </Button>
            <Button variant="outline" onClick={() => void router.invalidate()}>
              Refresh
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <div className="flex min-w-0 flex-col lg:flex-1 lg:overflow-y-auto lg:border-r lg:border-line">
          <section
            aria-label="Job"
            className="flex flex-col gap-4 border-b border-line px-4 py-5 lg:px-6"
          >
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  variant={runs[0] ? statusBadge(runs[0].status) : 'neutral'}
                  size="lg"
                  className="font-medium"
                >
                  {runs[0]?.status.replace('_', ' ') ?? 'No runs'}
                </Badge>
                <span className="text-[13px] text-muted-foreground">
                  {runs[0]
                    ? 'Latest observed job status'
                    : 'No run status available'}
                </span>
              </div>
              <h2 className="m-0 break-all font-mono text-lg font-medium">
                {job.id}
              </h2>
              <p className="m-0 text-sm text-muted-foreground">
                {job.description ?? 'No description supplied.'}
              </p>
            </div>
            <KeyValues
              className="text-[13.5px]"
              items={[
                ['Environment', env],
                ['Repository', job.repository_name],
                [
                  'Schedules',
                  schedules.length
                    ? schedules
                        .map((schedule) => `${schedule.id}: ${schedule.status}`)
                        .join(', ')
                    : 'None configured',
                ],
                ['Owner', 'Unavailable from API'],
                ['Average', 'Unavailable from API'],
                ['Last run', runs[0]?.created_at ?? 'No run observed'],
                [
                  'Runs shown',
                  `${runs.length} of at most 100 environment-scoped records`,
                ],
              ]}
            />
            <Dialog open={controlsOpen} onOpenChange={setControlsOpen}>
              <DialogContent className="max-w-[560px]">
                <DialogHeader>
                  <DialogTitle>Job controls</DialogTitle>
                  <DialogDescription>
                    <Mono>{job.id}</Mono> · {env}. Each action requires
                    confirmation.
                  </DialogDescription>
                </DialogHeader>
                <DialogBody className="gap-4">
                  <label className="flex flex-col gap-1 text-sm">
                    Partition key
                    <Input
                      value={partitionKey}
                      onChange={(event) => setPartitionKey(event.target.value)}
                      placeholder="For example, 2026-08-20"
                      maxLength={256}
                    />
                    <span className="text-xs text-muted-foreground">
                      Required for partitioned jobs. Leave empty for
                      unpartitioned jobs.
                    </span>
                  </label>
                  <ConfirmedAction
                    key={`launch:${env}:${job.id}:${partitionKey.trim()}`}
                    storageKey={`phlo:launch:${env}:${job.id}:${partitionKey.trim()}`}
                    confirmation={`I confirm a new run of ${job.id} in ${env}${partitionKey.trim() ? ` for partition ${partitionKey.trim()}` : ''}.`}
                    actionLabel="Launch run"
                    acceptedMessage="Dagster accepted the launch. Refresh to observe the run."
                    execute={async (idempotencyKey) => {
                      await launchJob({
                        data: {
                          env,
                          job_id: job.id,
                          idempotency_key: idempotencyKey,
                          confirmed: true,
                          partition_key: partitionKey.trim() || undefined,
                        },
                      })
                    }}
                  />
                  {schedules.map((schedule) =>
                    schedule.status === 'RUNNING' ||
                    schedule.status === 'STOPPED' ? (
                      <ConfirmedAction
                        key={`${env}:${schedule.id}:${schedule.status}`}
                        storageKey={`phlo:schedule:${env}:${schedule.id}:${schedule.status}`}
                        confirmation={`I confirm ${schedule.status === 'RUNNING' ? 'pausing' : 'resuming'} ${schedule.id} in ${env}.`}
                        actionLabel={
                          schedule.status === 'RUNNING'
                            ? `Pause ${schedule.id}`
                            : `Resume ${schedule.id}`
                        }
                        acceptedMessage={`Dagster accepted the schedule ${schedule.status === 'RUNNING' ? 'pause' : 'resume'}.`}
                        execute={async (idempotencyKey) => {
                          await changeSchedule({
                            data: {
                              env,
                              schedule_id: schedule.id,
                              action:
                                schedule.status === 'RUNNING'
                                  ? 'pause'
                                  : 'resume',
                              expected_status:
                                schedule.status === 'RUNNING'
                                  ? 'RUNNING'
                                  : 'STOPPED',
                              idempotency_key: idempotencyKey,
                              confirmed: true,
                            },
                          })
                          await router.invalidate()
                        }}
                      />
                    ) : (
                      <Button
                        key={schedule.id}
                        variant="outline"
                        disabled
                        title={`Unsupported schedule state: ${schedule.status}`}
                      >
                        Schedule control unavailable
                      </Button>
                    ),
                  )}
                </DialogBody>
              </DialogContent>
            </Dialog>
            <div className="flex flex-col gap-2.5">
              <div className="flex items-baseline gap-2">
                <Eyebrow>Recent runs</Eyebrow>
                <span className="text-[12.5px] text-muted-foreground">
                  oldest on the left · pick one to inspect it
                </span>
              </div>
              <div
                className="flex flex-wrap items-center gap-0.5"
                role="group"
                aria-label="Pick a run"
              >
                {[...runs]
                  .slice(0, 24)
                  .reverse()
                  .map((run) => (
                    <button
                      key={run.run_id}
                      type="button"
                      aria-pressed={selected?.run_id === run.run_id}
                      aria-label={`${run.status} run ${run.run_id}`}
                      title={`${run.status} · ${run.created_at}`}
                      onClick={() =>
                        void navigate({
                          search: (p) => ({ ...p, run: run.run_id }),
                        })
                      }
                      className="group flex h-10 w-3 cursor-pointer items-center justify-center rounded-[3px] lg:h-8"
                    >
                      <span
                        className={cn(
                          'h-[22px] w-2 rounded-[2px]',
                          runColor(run.status),
                          selected?.run_id === run.run_id
                            ? 'outline-2 outline-offset-2 outline-foreground'
                            : 'group-hover:opacity-75',
                        )}
                      />
                    </button>
                  ))}
              </div>
              <div className="flex flex-wrap gap-4 text-[12.5px] text-muted-foreground">
                {[
                  ['bg-sla-ok', 'Succeeded'],
                  ['bg-bad', 'Failed'],
                  ['bg-skip-line', 'Canceled'],
                  ['bg-warn-bar', 'In progress / queued'],
                ].map(([color, label]) => (
                  <span
                    key={label}
                    className="inline-flex items-center gap-1.5"
                  >
                    <span className={cn('h-3 w-2 rounded-[2px]', color)} />
                    {label}
                  </span>
                ))}
              </div>
            </div>
          </section>
          <section aria-labelledby="sib-h" className="hidden flex-col lg:flex">
            <div className="flex items-baseline gap-2 px-4 pt-4 pb-2 lg:px-6">
              <h2 id="sib-h" className="m-0 text-[13.5px] font-medium">
                {job.repository_name}
              </h2>
              <span className="text-[13px] text-muted-foreground">
                Sibling jobs unavailable from detail API
              </span>
              <Link
                to="/pipelines"
                search={{ env }}
                className="ml-auto text-[13px]"
              >
                All pipelines
              </Link>
            </div>
            <div
              className="grid h-8 grid-cols-[minmax(0,1fr)_148px] items-center gap-x-4 border-y border-line-soft bg-raised px-4 text-xs text-muted-foreground lg:px-6"
              aria-hidden
            >
              <span>Selected assets</span>
              <span className="text-right">Environment</span>
            </div>
            <ul className="m-0 list-none p-0">
              {job.selected_assets.map((key) => (
                <li
                  key={key.join('/')}
                  className="grid grid-cols-[minmax(0,1fr)_148px] items-center gap-x-4 border-b border-line-soft px-4 py-2 text-[13px] lg:px-6"
                >
                  <Link
                    to="/assets/$assetId"
                    params={{ assetId: key.join('/') }}
                    search={{ env }}
                    className="truncate font-mono text-[13px]"
                  >
                    {key.join('/')}
                  </Link>
                  <span className="text-right text-muted-foreground">
                    {env}
                  </span>
                </li>
              ))}
            </ul>
            {!job.selected_assets.length ? (
              <p className="m-0 px-4 py-5 text-sm text-muted-foreground lg:px-6">
                No selected assets or sibling-job records are available.
              </p>
            ) : null}
          </section>
        </div>
        <SelectedRun data={data} />
      </div>
    </>
  )
}

type ActionState =
  | { kind: 'idle' | 'pending' | 'accepted' }
  | { kind: 'failed'; message: string }

function ConfirmedAction({
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

function RetryControl({
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
    <div className="mt-auto flex flex-col gap-3 border-t border-line pt-4">
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
            to="/pipelines/$jobName"
            params={{ jobName: jobId }}
            search={{ env, run: state.runId }}
            className="inline-block"
          >
            View retry run
          </Link>
        </p>
      ) : null}
    </div>
  )
}
