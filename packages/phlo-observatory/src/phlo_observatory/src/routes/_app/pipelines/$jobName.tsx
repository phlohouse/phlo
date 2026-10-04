/** Defines pipeline details, run events, and guarded run operations. */
import * as React from 'react'
import {
  Link,
  createFileRoute,
  redirect,
  useRouter,
} from '@tanstack/react-router'
import { z } from 'zod'
import {
  changeSchedule,
  getPipelineJob,
  launchJob,
} from '@/lib/data/api/pipelines'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { Mono } from '@/components/phlo/status'
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
import {
  pipelineSearchSchema,
  runBadge,
  runColor,
  runDuration,
} from '@/components/pipelines/bits'
import { ConfirmedAction } from '@/components/pipelines/controls'
import { formatRunLogTimestamp } from '@/components/pipelines/run-logs'

export const Route = createFileRoute('/_app/pipelines/$jobName')({
  validateSearch: pipelineSearchSchema.extend({
    run: z.string().min(1).optional(),
  }),
  loaderDeps: ({ search }) => ({ env: search.env, run: search.run }),
  loader: ({ params, deps }) => {
    if (deps.run)
      throw redirect({
        to: '/pipelines/$jobName/runs/$runId',
        params: { jobName: params.jobName, runId: deps.run },
        search: { env: deps.env },
        replace: true,
      })
    return getPipelineJob({ data: { id: params.jobName, env: deps.env } })
  },
  head: ({ params }) => ({ meta: [{ title: `${params.jobName} · phlo` }] }),
  component: PipelinePage,
})

type PipelineData = Awaited<ReturnType<typeof getPipelineJob>>

function JobSummary({ data }: { data: PipelineData }) {
  const { job, runs, schedules, env } = data
  const durations = runs.flatMap((run) =>
    run.duration_seconds === null ? [] : [run.duration_seconds],
  )
  const details: Array<[string, React.ReactNode]> = [
    ['Environment', env],
    ['Repository', job.repository_name],
    ['Domain', job.domain ?? 'Not declared'],
    ['Source', job.source ?? 'Not declared'],
    [
      'Schedules',
      schedules.length
        ? schedules
            .map((schedule) => `${schedule.id}: ${schedule.status}`)
            .join(', ')
        : 'None configured',
    ],
    ['Owner', job.owners.join(' · ') || 'Not declared'],
    [
      'Average',
      durations.length
        ? runDuration(
            durations.reduce((sum, value) => sum + value, 0) / durations.length,
          )
        : 'No completed durations',
    ],
    [
      'Last run',
      runs[0] ? formatRunLogTimestamp(runs[0].created_at) : 'No run observed',
    ],
    ['Runs shown', `${runs.length} in this view`],
  ]
  return (
    <>
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant={runs[0] ? runBadge(runs[0].status) : 'neutral'}
            size="lg"
            className="font-medium"
          >
            {runs[0]?.status.replace('_', ' ') ?? 'No runs'}
          </Badge>
          <span className="text-[13px] text-muted-foreground">
            {runs[0] ? 'Latest observed job status' : 'No run status available'}
          </span>
        </div>
        <h2 className="m-0 break-all font-mono text-lg font-medium">
          {job.id}
        </h2>
        <p className="m-0 text-sm text-muted-foreground">
          {job.description ?? 'No description supplied.'}
        </p>
      </div>
      <dl className="m-0 grid grid-cols-2 gap-x-5 gap-y-3 text-[13.5px] sm:grid-cols-3 xl:grid-cols-5">
        {details.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="m-0 break-words">{value}</dd>
          </div>
        ))}
      </dl>
    </>
  )
}

function PipelinePage() {
  const data = Route.useLoaderData()
  const { job, runs, schedules, env } = data
  const navigate = Route.useNavigate()
  const router = useRouter()
  const [partitionKey, setPartitionKey] = React.useState('')
  const [controlsOpen, setControlsOpen] = React.useState<
    'launch' | 'schedule' | null
  >(null)
  const [historyLimit, setHistoryLimit] = React.useState(50)

  return (
    <>
      <PageHeader
        crumbs={[{ label: 'Pipelines', to: '/pipelines' }]}
        title={<Mono className="text-[13.5px]">{job.id}</Mono>}
        meta={`${job.domain ?? 'Unknown domain'} · ${env}`}
        actions={
          <>
            {schedules.length ? (
              <Button
                variant="outline"
                onClick={() => setControlsOpen('schedule')}
              >
                {schedules.every((schedule) => schedule.status === 'STOPPED')
                  ? 'Resume schedule'
                  : 'Pause schedule'}
              </Button>
            ) : null}
            <Button variant="outline" onClick={() => setControlsOpen('launch')}>
              Launch run
            </Button>
            <Button variant="outline" onClick={() => void router.invalidate()}>
              Refresh
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <div className="flex min-w-0 flex-col">
          <section
            aria-label="Job"
            className="flex flex-col gap-4 border-b border-line px-4 py-5 lg:px-6"
          >
            <JobSummary data={data} />
            <Dialog
              open={controlsOpen !== null}
              onOpenChange={(open) => {
                if (!open) setControlsOpen(null)
              }}
            >
              <DialogContent className="max-w-[560px]">
                <DialogHeader>
                  <DialogTitle>
                    {controlsOpen === 'launch'
                      ? 'Launch run'
                      : 'Schedule controls'}
                  </DialogTitle>
                  <DialogDescription>
                    <Mono>{job.id}</Mono> · {env}. Each action requires
                    confirmation.
                  </DialogDescription>
                </DialogHeader>
                <DialogBody className="gap-4">
                  {controlsOpen === 'launch' ? (
                    <>
                      <label className="flex flex-col gap-1 text-sm">
                        Partition key
                        <Input
                          value={partitionKey}
                          onChange={(event) =>
                            setPartitionKey(event.target.value)
                          }
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
                        acceptedMessage="Dagster accepted the launch. Opening the run…"
                        execute={async (idempotencyKey) => {
                          const result = await launchJob({
                            data: {
                              env,
                              job_id: job.id,
                              idempotency_key: idempotencyKey,
                              confirmed: true,
                              partition_key: partitionKey.trim() || undefined,
                            },
                          })
                          setControlsOpen(null)
                          await navigate({
                            to: '/pipelines/$jobName/runs/$runId',
                            params: { jobName: job.id, runId: result.run_id },
                            search: { env },
                          })
                        }}
                      />
                    </>
                  ) : null}
                  {controlsOpen === 'schedule'
                    ? schedules.map((schedule) =>
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
                      )
                    : null}
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
                    <Link
                      key={run.run_id}
                      to="/pipelines/$jobName/runs/$runId"
                      params={{ jobName: job.id, runId: run.run_id }}
                      search={{ env }}
                      aria-label={`${run.status} run ${run.run_id}`}
                      title={`${run.status} · ${run.created_at}`}
                      className="group flex h-10 w-3 cursor-pointer items-center justify-center rounded-[3px] lg:h-8"
                    >
                      <span
                        className={cn(
                          'h-[22px] w-2 rounded-[2px]',
                          runColor(run.status),
                          'group-hover:opacity-75',
                        )}
                      />
                    </Link>
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
          <section
            aria-label="Run history"
            className="flex flex-col gap-3 border-b border-line px-4 py-5 lg:px-6"
          >
            <Eyebrow>Run history · {runs.length}</Eyebrow>
            <div className="max-h-80 overflow-y-auto">
              {runs.slice(0, historyLimit).map((run) => (
                <Link
                  key={run.run_id}
                  to="/pipelines/$jobName/runs/$runId"
                  params={{ jobName: job.id, runId: run.run_id }}
                  search={{ env }}
                  className="flex min-h-11 w-full items-center gap-3 border-b border-line-soft px-2 text-left text-xs hover:bg-primary-soft"
                >
                  <span
                    className={cn(
                      'size-2 shrink-0 rounded-sm',
                      runColor(run.status),
                    )}
                  />
                  <span className="min-w-0 flex-1 truncate font-mono">
                    {run.run_id}
                  </span>
                  <time className="hidden sm:inline">{run.created_at}</time>
                  <span>{run.status}</span>
                </Link>
              ))}
            </div>
            {historyLimit < runs.length ? (
              <Button
                variant="outline"
                onClick={() => setHistoryLimit((limit) => limit + 50)}
              >
                Show older runs
              </Button>
            ) : null}
            <Eyebrow>Summary · latest {data.summary.scanned_runs} runs</Eyebrow>
            <p className="m-0 text-sm text-muted-foreground">
              {Object.entries(data.summary.counts_by_status)
                .map(([status, count]) => `${count} ${status.toLowerCase()}`)
                .join(' · ') || 'No recorded runs'}
            </p>
            <p className="m-0 text-xs text-muted-foreground">
              Duration buckets, seconds:{' '}
              {Object.entries(data.summary.duration_histogram_seconds)
                .map(([bucket, count]) => `${bucket}: ${count}`)
                .join(' · ')}
            </p>
            {data.patterns.items.map((pattern) => (
              <p key={pattern.kind} className="m-0 text-sm">
                {pattern.count}{' '}
                {pattern.kind === 'failure' ? 'failed runs' : 'slow runs'} in
                the latest {data.patterns.scanned_runs} runs.
              </p>
            ))}
          </section>
          <section aria-labelledby="sib-h" className="hidden flex-col lg:flex">
            <div className="flex items-baseline gap-2 px-4 pt-4 pb-2 lg:px-6">
              <h2 id="sib-h" className="m-0 text-[13.5px] font-medium">
                {job.domain ?? 'Unknown domain'}
              </h2>
              <span className="text-[13px] text-muted-foreground">
                {data.siblings.length} jobs in this domain
              </span>
              <Link
                to="/pipelines"
                search={(previous) => ({ ...previous, env })}
                className="ml-auto text-[13px]"
              >
                All pipelines
              </Link>
            </div>
            {data.siblings.map((sibling) => (
              <Link
                key={sibling.id}
                to="/pipelines/$jobName"
                params={{ jobName: sibling.id }}
                search={{ env }}
                aria-current={sibling.id === job.id ? 'page' : undefined}
                className="min-h-10 border-b border-line-soft px-6 py-2 font-mono text-sm"
              >
                {sibling.id}
              </Link>
            ))}
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
      </div>
    </>
  )
}
