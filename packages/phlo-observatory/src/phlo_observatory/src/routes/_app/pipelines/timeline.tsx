/** Defines the time-bucketed pipeline run timeline route. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { ChevronRightIcon } from 'lucide-react'
import { getRunTimeline } from '@/lib/data/api/pipelines'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { ViewSwitch, runColor } from '@/components/pipelines/bits'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/pipelines/timeline')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getRunTimeline({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Run timeline · phlo' }] }),
  component: TimelinePage,
})

const bucketMs = 30 * 60 * 1000

function TimelinePage() {
  const { jobs, runs, env, observed_at } = Route.useLoaderData()
  const router = useRouter()
  const end =
    Math.floor(Date.parse(observed_at) / bucketMs) * bucketMs + bucketMs
  const start = end - 48 * bucketMs
  const groups = [...new Set(jobs.map((job) => job.repository_name))]
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
        crumbs={[{ label: 'Pipelines', to: '/pipelines' }]}
        title="Run timeline"
        meta={`${jobs.length} jobs · ${env} · 30-minute buckets`}
        actions={
          <>
            <div className="inline-flex rounded-lg border border-border bg-raised p-0.5 text-[13px]">
              <span className="rounded-md bg-card px-2.5 py-1 shadow-[0_0_0_1px_var(--border)]">
                24 h
              </span>
              <button
                disabled
                title="The API read is bounded to the latest 100 runs"
                className="px-2.5 text-muted-foreground opacity-50"
              >
                7 d
              </button>
            </div>
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
            {jobs.length} jobs · last 24 h · each square is 30 minutes. Scroll
            sideways to see the whole day.
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
                <span className="border border-dashed border-line-soft text-center text-[10px]">
                  Unknown — maintenance and incident events are not exposed by
                  this API
                </span>
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
                      {
                        jobs.filter((job) => job.repository_name === group)
                          .length
                      }{' '}
                      jobs
                    </span>
                  </button>
                  {open.has(group)
                    ? jobs
                        .filter((job) => job.repository_name === group)
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
                                    title={`${label}. This is not proof of no runs.`}
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
          <div className="border-b border-line-soft py-2">
            <h3 className="m-0 text-[13.5px] font-medium text-foreground">
              Unknown from bounded history
            </h3>
            <p className="mt-1 mb-0">
              Cross-job correlations require complete run history and are not
              inferred.
            </p>
          </div>
          <Eyebrow>History coverage</Eyebrow>
          <p className="m-0">
            This grid uses the latest {runs.length} records from a bounded
            environment-scoped API read, up to 100. It does not prove complete
            24-hour coverage.
          </p>
          <p className="m-0">
            Maintenance events and cross-job correlations are not connected in
            this view.
          </p>
        </aside>
      </div>
    </>
  )
}
