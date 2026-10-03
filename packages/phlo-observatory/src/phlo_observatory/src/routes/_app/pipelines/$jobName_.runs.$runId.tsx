/** Opens a scoped run with its details, full-width logs, and guarded controls. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { cancelRun, getPipelineJob } from '@/lib/data/api/pipelines'
import { KeyValues, PageHeader } from '@/components/phlo/page'
import { Mono } from '@/components/phlo/status'
import { EmptyState } from '@/components/phlo/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { runBadge, runDuration } from '@/components/pipelines/bits'
import { overlappingMaintenance } from '@/components/pipelines/evidence'
import { ConfirmedAction, RetryControl } from '@/components/pipelines/controls'

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
): React.ComponentProps<typeof KeyValues>['items'] {
  return [
    ['Duration', runDuration(run.duration_seconds)],
    ['Created', run.created_at],
    ['Started', run.started_at ?? 'Not started'],
    ['Ended', run.ended_at ?? 'Not ended'],
    [
      'Trigger',
      run.tags?.['dagster/schedule_name'] ??
        run.tags?.['dagster/sensor_name'] ??
        run.tags?.['phlo/operation'] ??
        'Not declared',
    ],
  ]
}

function RunEvents({ events }: { events: RunData['events'] }) {
  const [expanded, setExpanded] = React.useState(false)
  const items =
    events?.items.filter((event) => event.event_type !== 'LOG_MESSAGE') ?? []
  if (!items.length)
    return (
      <p className="m-0 text-sm text-muted-foreground">No events observed.</p>
    )
  return (
    <>
      <ul className="m-0 flex list-none flex-col gap-2 p-0 text-xs">
        {items.slice(0, expanded ? undefined : 10).map((event, index) => (
          <li key={index} className="border-b border-line-soft pb-2">
            <div className="break-words font-mono">
              {event.step_key ?? 'Run'} · {event.event_type}
            </div>
            <time className="text-muted-foreground">{event.timestamp}</time>
          </li>
        ))}
      </ul>
      {items.length > 10 ? (
        <Button
          variant="outline"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? 'Show fewer events' : `Show all ${items.length} events`}
        </Button>
      ) : null}
    </>
  )
}

function RunPage() {
  const data = Route.useLoaderData()
  const { selected, events, env, job } = data
  const router = useRouter()
  React.useEffect(() => {
    if (
      !selected ||
      ['SUCCESS', 'FAILURE', 'CANCELED'].includes(selected.status)
    )
      return
    const timer = window.setInterval(() => {
      if (!router.state.isLoading) void router.invalidate()
    }, 5000)
    return () => window.clearInterval(timer)
  }, [env, job.id, selected?.run_id, selected?.status, router])

  if (!selected) return <EmptyState title="No run observed" />
  return (
    <>
      <PageHeader
        crumbs={[
          { label: 'Pipelines', to: '/pipelines' },
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
            <Mono className="break-all text-sm">{selected.run_id}</Mono>
            <span className="text-sm text-muted-foreground">{env}</span>
          </div>
          <KeyValues
            items={[
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
            ]}
          />
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
        <section
          aria-labelledby="run-logs-heading"
          className="flex min-w-0 flex-col gap-3 border-b border-line px-4 py-5 lg:px-6"
        >
          <h2 id="run-logs-heading" className="m-0 text-sm font-medium">
            Logs
          </h2>
          <pre className="m-0 max-h-96 min-w-0 overflow-auto rounded-lg border border-line bg-sunken p-3 font-mono text-xs whitespace-pre-wrap break-words">
            {events?.items
              .map(
                (event) =>
                  `${event.timestamp} ${event.event_type} ${event.step_key ?? ''}\n${event.message}`,
              )
              .join('\n\n') || 'No log evidence returned.'}
          </pre>
        </section>
        <section
          aria-labelledby="run-events-heading"
          className="flex flex-col gap-3 px-4 py-5 lg:px-6"
        >
          <h2 id="run-events-heading" className="m-0 text-sm font-medium">
            Run events
          </h2>
          <RunEvents key={selected.run_id} events={events} />
          {events?.truncated ? (
            <p className="m-0 text-xs text-muted-foreground">
              More events are available from the API.
            </p>
          ) : null}
        </section>
      </div>
    </>
  )
}
