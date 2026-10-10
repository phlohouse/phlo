/** Opens a scoped run with its details, full-width logs, and guarded controls. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import {
  cancelRun,
  getPipelineJob,
  getRunLogPage,
} from '@/lib/data/api/pipelines'
import { PageHeader } from '@/components/phlo/page'
import { Mono } from '@/components/phlo/status'
import { EmptyState } from '@/components/phlo/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { runBadge, runDuration } from '@/components/pipelines/bits'
import { overlappingMaintenance } from '@/components/pipelines/evidence'
import { ConfirmedAction, RetryControl } from '@/components/pipelines/controls'
import {
  RunLogsPanel,
  formatRunLogTimestamp,
} from '@/components/pipelines/run-logs'

export const Route = createFileRoute('/_app/pipelines/$jobName_/runs/$runId')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ params, deps }) =>
    getPipelineJob({
      data: { id: params.jobName, run: params.runId, ...deps },
    }),
  head: ({ params }) => ({ meta: [{ title: `Run ${params.runId} · phlo` }] }),
  component: RunPage,
})

type RunData = Awaited<ReturnType<typeof getPipelineJob>>

function runDetails(
  run: NonNullable<RunData['selected']>,
): Array<[string, React.ReactNode]> {
  return [
    ['Duration', runDuration(run.duration_seconds)],
    ['Created', formatRunLogTimestamp(run.created_at)],
    [
      'Started',
      run.started_at ? formatRunLogTimestamp(run.started_at) : 'Not started',
    ],
    ['Ended', run.ended_at ? formatRunLogTimestamp(run.ended_at) : 'Not ended'],
    [
      'Trigger',
      run.tags?.['dagster/schedule_name'] ??
        run.tags?.['dagster/sensor_name'] ??
        run.tags?.['phlo/operation'] ??
        'Not declared',
    ],
  ]
}

function RunPage() {
  const data = Route.useLoaderData()
  const { selected, events, env, job } = data
  const router = useRouter()
  if (!selected) return <EmptyState title="No run observed" />
  const details: Array<[string, React.ReactNode]> = [
    [
      'Job',
      <Link
        to="/pipelines/$jobName"
        params={{ jobName: job.id }}
        search={{ env }}
        className="break-all font-mono"
      >
        {job.id}
      </Link>,
    ],
    ...runDetails(selected),
  ]
  return (
    <>
      <PageHeader
        crumbs={[
          { label: 'Jobs', to: '/pipelines' },
          {
            label: 'Job',
            to: `/pipelines/${encodeURIComponent(job.id)}`,
          },
        ]}
        title={<Mono>Run {selected.run_id}</Mono>}
        meta={env}
        actions={
          <Button variant="outline" onClick={() => void router.invalidate()}>
            Refresh
          </Button>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <section
          aria-label="Run details"
          className="flex flex-col gap-4 border-b border-line px-4 py-5 lg:px-6"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={runBadge(selected.status)} className="font-medium">
              {selected.status.replace('_', ' ')}
            </Badge>
            <span className="text-sm text-muted-foreground">{env}</span>
          </div>
          <dl className="m-0 grid grid-cols-2 gap-x-5 gap-y-3 text-sm sm:grid-cols-3 xl:grid-cols-6">
            {details.map(([label, value]) => (
              <div key={label} className="min-w-0">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="m-0 break-words">{value}</dd>
              </div>
            ))}
          </dl>
          {overlappingMaintenance(
            data.maintenance.items,
            Date.parse(selected.started_at ?? selected.created_at),
            selected.ended_at ? Date.parse(selected.ended_at) : Date.now(),
          ).map((window) => (
            <p
              key={window.id}
              className="m-0 rounded-lg bg-branch-soft p-2 text-sm"
            >
              Overlaps planned maintenance: {window.description ?? window.id}.{' '}
              {window.starts_at} to {window.ends_at}.
            </p>
          ))}
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
        </section>
        <RunLogsPanel
          key={`${env}:${job.id}:${selected.run_id}`}
          runId={selected.run_id}
          events={events}
          loadPage={(cursor, signal) =>
            getRunLogPage({
              data: { env, id: job.id, run: selected.run_id, cursor },
              signal,
            })
          }
        />
      </div>
    </>
  )
}
