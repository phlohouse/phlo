/** Defines the time-bucketed pipeline run timeline route. */
import * as React from 'react'
import {
  Link,
  createFileRoute,
  getRouteApi,
  useRouter,
} from '@tanstack/react-router'
import { ChevronRightIcon } from 'lucide-react'
import { z } from 'zod'
import { getRunTimeline } from '@/lib/data/api/pipelines'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import {
  ViewSwitch,
  pipelineSearchSchema,
  runColor,
} from '@/components/pipelines/bits'
import {
  correlatedFailures,
  overlappingMaintenance,
  pipelineGroupName,
  pipelineMatches,
} from '@/components/pipelines/evidence'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/toggle-group'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/pipelines/timeline')({
  validateSearch: pipelineSearchSchema.extend({
    range: z.enum(['24h', '7d']).default('24h'),
  }),
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getRunTimeline({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Run timeline · phlo' }] }),
  component: TimelinePage,
})

function TimelinePage() {
  const {
    jobs: inventory,
    runs,
    env,
    observed_at,
    maintenance,
  } = Route.useLoaderData()
  const search = Route.useSearch()
  const { range, by } = search
  const { me } = getRouteApi('/_app').useLoaderData()
  const jobs = inventory.filter((job) =>
    pipelineMatches(
      job,
      runs.find((run) => run.job_id === job.id),
      search,
      me,
    ),
  )
  const groupName = (job: (typeof jobs)[number]) => pipelineGroupName(job, by)
  const navigate = Route.useNavigate()
  const router = useRouter()
  const bucketMs = (range === '24h' ? 30 : 210) * 60 * 1000
  const end =
    Math.floor(Date.parse(observed_at) / bucketMs) * bucketMs + bucketMs
  const start = end - 48 * bucketMs
  const groups = [...new Set(jobs.map(groupName))]
  const inRange = runs.filter(
    (run) =>
      Date.parse(run.created_at) >= start && Date.parse(run.created_at) < end,
  )
  const correlations = correlatedFailures(jobs, inRange)
  const [open, setOpen] = React.useState(() => new Set(groups))
  const toggle = (group: string) =>
    setOpen((current) => {
      const next = new Set(current)
      next.has(group) ? next.delete(group) : next.add(group)
      return next
    })
  return (
    <>
      <PageHeader
        crumbs={[{ label: 'Jobs', to: '/pipelines' }]}
        title="Run timeline"
        meta={`${jobs.length} jobs · ${env} · ${range === '24h' ? '30-minute' : '3.5-hour'} buckets`}
        actions={
          <>
            <Segmented
              aria-label="Time range"
              value={range}
              options={[
                { value: '24h', label: '24 h' },
                { value: '7d', label: '7 d' },
              ]}
              onValueChange={(value) =>
                void navigate({
                  search: (previous) => ({ ...previous, range: value }),
                  replace: true,
                })
              }
            />
            <Button variant="outline" onClick={() => void router.invalidate()}>
              Refresh
            </Button>
            <ViewSwitch current="timeline" env={env} />
          </>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <section
          aria-label="Runs by job"
          className="flex min-w-0 flex-col gap-3 px-4 py-4 lg:flex-1 lg:overflow-auto lg:px-5"
        >
          <p className="m-0 text-[12.5px] text-muted-foreground md:hidden">
            {jobs.length} jobs · {range} · scroll sideways to see the whole
            range.
          </p>
          <p className="m-0 text-xs text-muted-foreground">
            {new Date(start).toISOString()} to {new Date(end).toISOString()}.
            Buckets use run creation time. Failure takes priority when a bucket
            contains several statuses.
          </p>
          <div
            tabIndex={0}
            role="region"
            aria-label="Run timeline grid, scrolls sideways"
            className="overflow-x-auto"
          >
            <div className="min-w-[720px]">
              <div className="grid grid-cols-[148px_minmax(0,1fr)] gap-x-3 text-xs text-muted-foreground">
                <span>Job</span>
                <div className="flex justify-between">
                  <time>{new Date(start).toISOString().slice(11, 16)} UTC</time>
                  <time>{new Date(end).toISOString().slice(11, 16)} UTC</time>
                </div>
              </div>
              <div className="mt-1 grid h-[18px] grid-cols-[148px_minmax(0,1fr)] gap-x-3 text-[11.5px] text-muted-foreground">
                <span>Events</span>
                <div
                  className="grid grid-cols-[repeat(48,minmax(0,1fr))] gap-0.5"
                  aria-label="Maintenance windows"
                >
                  {Array.from({ length: 48 }, (_, index) => {
                    const windows = overlappingMaintenance(
                      maintenance.items,
                      start + index * bucketMs,
                      start + (index + 1) * bucketMs,
                    )
                    return (
                      <span
                        key={index}
                        className={cn(
                          'rounded-[2px]',
                          windows.length
                            ? 'bg-branch-soft'
                            : 'border border-line-soft',
                        )}
                        title={
                          windows.length
                            ? windows
                                .map(
                                  (window) =>
                                    `${window.description ?? window.id}: ${window.starts_at} to ${window.ends_at}`,
                                )
                                .join('; ')
                            : maintenance.status === 'unavailable'
                              ? 'Maintenance policy unavailable'
                              : 'No planned maintenance'
                        }
                      />
                    )
                  })}
                </div>
              </div>
              {groups.map((group) => (
                <div key={group} className="mt-2.5">
                  <button
                    type="button"
                    aria-expanded={open.has(group)}
                    onClick={() => toggle(group)}
                    className="sticky left-0 z-[1] flex min-h-10 items-center gap-2 bg-card text-left text-[12.5px] font-medium lg:min-h-[26px]"
                  >
                    <ChevronRightIcon
                      className={cn(
                        'size-3 text-faint transition-transform',
                        open.has(group) && 'rotate-90',
                      )}
                    />
                    {group}{' '}
                    <span className="font-normal text-muted-foreground">
                      {jobs.filter((job) => groupName(job) === group).length}{' '}
                      jobs
                    </span>
                  </button>
                  {open.has(group)
                    ? jobs
                        .filter((job) => groupName(job) === group)
                        .map((job) => (
                          <div
                            key={job.id}
                            className="mt-0.5 grid h-3.5 grid-cols-[148px_minmax(0,1fr)] items-center gap-x-3"
                          >
                            <Link
                              to="/pipelines/$jobName"
                              params={{ jobName: job.id }}
                              search={{ env }}
                              className="sticky left-0 z-[1] truncate bg-card font-mono text-[11.5px] text-foreground"
                            >
                              {job.id}
                            </Link>
                            <div
                              className="grid grid-cols-[repeat(48,minmax(0,1fr))] gap-0.5"
                              aria-label={`Timeline for ${job.id}`}
                            >
                              {Array.from({ length: 48 }, (_, index) => {
                                const from = start + index * bucketMs
                                const entries = runs.filter(
                                  (run) =>
                                    run.job_id === job.id &&
                                    Date.parse(run.created_at) >= from &&
                                    Date.parse(run.created_at) <
                                      from + bucketMs,
                                )
                                const chosen =
                                  entries.find(
                                    (run) => run.status === 'FAILURE',
                                  ) ?? entries[0]
                                const label = `${new Date(from).toISOString()} · ${entries.length} observed runs${entries.length ? ` · ${entries.map((run) => run.status).join(', ')}` : ''}`
                                return chosen ? (
                                  <Link
                                    key={index}
                                    to="/pipelines/$jobName"
                                    params={{ jobName: job.id }}
                                    search={{ env, run: chosen.run_id }}
                                    title={label}
                                    aria-label={label}
                                    className={cn(
                                      'h-3 rounded-[2px]',
                                      runColor(chosen.status),
                                    )}
                                  />
                                ) : (
                                  <span
                                    key={index}
                                    title={`${label}. No recorded run in this bucket.`}
                                    className="h-3 rounded-[2px] border border-line-soft"
                                  />
                                )
                              })}
                            </div>
                          </div>
                        ))
                    : null}
                </div>
              ))}
            </div>
          </div>
          {!jobs.length ? (
            <EmptyState title="No jobs in this environment" />
          ) : null}
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
            {[
              ['bg-sla-ok', 'Succeeded'],
              ['bg-bad', 'Failed'],
              ['bg-skip-line', 'Canceled'],
              ['bg-warn-bar', 'Other observed state'],
              ['border border-line-soft', 'No fetched evidence'],
            ].map(([color, label]) => (
              <span key={label} className="inline-flex items-center gap-1.5">
                <span className={cn('h-3 w-2 rounded-[2px]', color)} />
                {label}
              </span>
            ))}
          </div>
        </section>
        <aside
          aria-labelledby="patterns-h"
          className="flex shrink-0 flex-col gap-3 border-t border-line bg-raised px-4 py-4 text-[13px] text-muted-foreground lg:w-[300px] lg:border-t-0 lg:border-l lg:px-[22px]"
        >
          <Eyebrow id="patterns-h">Patterns across jobs</Eyebrow>
          {correlations.map((group) => (
            <div
              key={`${group.source}:${group.runs[0].run_id}`}
              className="border-b border-line-soft py-2"
            >
              <h3 className="m-0 text-[13.5px] font-medium text-foreground">
                {group.source} · coincident failures
              </h3>
              <p>
                {new Set(group.runs.map((run) => run.job_id)).size} jobs failed
                within 10 minutes. Timing and declared source match; a shared
                cause is not confirmed.
              </p>
              {group.runs.map((run) => (
                <Link
                  key={run.run_id}
                  to="/pipelines/$jobName"
                  params={{ jobName: run.job_id }}
                  search={{ env, run: run.run_id }}
                  className="block truncate font-mono text-xs"
                >
                  {run.job_id} · {run.created_at}
                </Link>
              ))}
            </div>
          ))}
          {!correlations.length ? (
            <p>
              No coincident cross-job failures with a declared common source.
            </p>
          ) : null}
          <Eyebrow>History coverage</Eyebrow>
          <p className="m-0">
            {inRange.length} recorded runs in this range, from {runs.length}{' '}
            paginated environment-scoped records.
          </p>
          <p className="m-0">
            {maintenance.status === 'unavailable'
              ? 'Maintenance policy unavailable.'
              : `${maintenance.items.length} configured maintenance windows. An annotation does not prove maintenance caused a failure.`}
          </p>
        </aside>
      </div>
    </>
  )
}
