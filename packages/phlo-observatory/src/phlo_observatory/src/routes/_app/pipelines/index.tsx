/** Pipeline catalogue composed from environment-scoped API observations. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import {
  ChevronRightIcon,
  SearchIcon,
  SlidersHorizontalIcon,
} from 'lucide-react'
import { z } from 'zod'
import type { ApiRun } from '@/lib/data/api/pipelines'
import { getPipelineList } from '@/lib/data/api/pipelines'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import {
  Facet,
  HealthMix,
  ViewSwitch,
  runColor,
} from '@/components/pipelines/bits'
import { Card } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Dot } from '@/components/phlo/status'

export const Route = createFileRoute('/_app/pipelines/')({
  validateSearch: z.object({ q: z.string().default('') }),
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getPipelineList({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Pipelines · phlo' }] }),
  component: PipelinesPage,
})

type Job = Awaited<ReturnType<typeof getPipelineList>>['jobs'][number]
type ObservedJob = Job & {
  runs: Array<ApiRun>
  latest?: ApiRun
  failing: boolean
}
const rowGrid =
  'grid grid-cols-[14px_minmax(0,1.3fr)_110px_minmax(0,1.3fr)_148px_92px_110px] items-center gap-x-3 px-4'

const runStates = {
  failed: { label: 'Failed', tone: 'bad' },
  succeeded: { label: 'Succeeded', tone: 'ok' },
  other: { label: 'Other run states', tone: 'info' },
  unknown: { label: 'No run observed', tone: 'neutral' },
} as const

export function observedRunState(latest?: ApiRun) {
  if (!latest) return 'unknown'
  if (latest.status === 'FAILURE') return 'failed'
  if (latest.status === 'SUCCESS') return 'succeeded'
  return 'other'
}

function PipelinesPage() {
  const { jobs, runs, env } = Route.useLoaderData()
  const { q } = Route.useSearch()
  const navigate = Route.useNavigate()
  const router = useRouter()
  const [states, setStates] = React.useState({
    failed: true,
    succeeded: true,
    other: true,
    unknown: true,
  })
  const [open, setOpen] = React.useState<Set<string>>(() => new Set())
  const [filtersOpen, setFiltersOpen] = React.useState(false)
  const observed = React.useMemo<Array<ObservedJob>>(
    () =>
      jobs.map((job) => {
        const history = runs
          .filter((run) => run.job_id === job.id)
          .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
          .slice(0, 24)
        return {
          ...job,
          runs: history,
          latest: history[0],
          failing: history[0]?.status === 'FAILURE',
        }
      }),
    [jobs, runs],
  )
  const needle = q.trim().toLowerCase()
  const visible = observed.filter(
    (job) =>
      job.id.toLowerCase().includes(needle) &&
      states[observedRunState(job.latest)],
  )
  const attention = visible.filter((job) => job.failing)
  const grouped = [
    ...new Set(
      visible.filter((job) => !job.failing).map((job) => job.repository_name),
    ),
  ].map((name) => ({
    name,
    jobs: visible.filter((job) => !job.failing && job.repository_name === name),
  }))
  const success = runs.filter((run) => run.status === 'SUCCESS').length
  const failed = runs.filter((run) => run.status === 'FAILURE').length
  const unknown =
    jobs.length -
    observed.filter((job) => job.latest?.status === 'SUCCESS' || job.failing)
      .length
  const setQuery = (value: string) =>
    void navigate({
      search: (previous) => ({ ...previous, q: value }),
      replace: true,
    })
  const toggleGroup = (name: string) =>
    setOpen((current) => {
      const next = new Set(current)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  const facets = (
    <>
      <Facet title="Status">
        {(Object.keys(runStates) as Array<keyof typeof runStates>).map(
          (state) => (
            <FacetItem
              key={state}
              checked={states[state]}
              onChange={(checked) =>
                setStates((previous) => ({ ...previous, [state]: checked }))
              }
              count={
                observed.filter((job) => observedRunState(job.latest) === state)
                  .length
              }
            >
              <Dot tone={runStates[state].tone} />
              {runStates[state].label}
            </FacetItem>
          ),
        )}
      </Facet>
      <Facet title="Source">
        <DisabledFacet label="Unknown — API metadata unavailable" />
      </Facet>
      <Facet title="Owner">
        <DisabledFacet label="Unknown — API metadata unavailable" />
      </Facet>
      <Facet title="Saved views">
        <DisabledFacet label="Not supported by API" />
      </Facet>
    </>
  )
  const searchBox = (id: string, className?: string) => (
    <label
      htmlFor={id}
      className={cn(
        'flex h-8 items-center gap-2 rounded-lg border border-border bg-raised px-2.5 text-muted-foreground focus-within:border-primary',
        className,
      )}
    >
      <SearchIcon className="size-3.5" />
      <span className="sr-only">Find a job</span>
      <input
        id={id}
        type="search"
        value={q}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Find a job"
        className="min-w-0 flex-1 border-0 bg-transparent text-[13.5px] text-foreground outline-none placeholder:text-faint"
      />
    </label>
  )
  return (
    <>
      <PageHeader
        title="Pipelines"
        meta={`${jobs.length} jobs · ${runs.length} observed runs · ${env}`}
        actions={
          <>
            {searchBox('job-search', 'hidden w-[220px] lg:flex')}
            <span className="hidden text-[13px] text-muted-foreground sm:inline">
              Group by
            </span>
            <div className="inline-flex rounded-lg border border-border bg-raised p-0.5 text-[13px]">
              <span className="rounded-md bg-card px-2.5 py-1 shadow-[0_0_0_1px_var(--border)]">
                Repository
              </span>
              <button
                disabled
                title="Owner metadata unavailable"
                className="px-2.5 text-muted-foreground opacity-50"
              >
                Owner
              </button>
              <button
                disabled
                title="Source metadata unavailable"
                className="px-2.5 text-muted-foreground opacity-50"
              >
                Source
              </button>
            </div>
            <Button variant="outline" onClick={() => void router.invalidate()}>
              Refresh
            </Button>
            <ViewSwitch current="list" env={env} />
          </>
        }
      />
      <div className="hidden shrink-0 items-center gap-4 border-b border-line px-5 py-3 md:flex">
        <HealthMix
          className="h-2.5 flex-1 gap-0.5"
          label={`${failed} failing runs, ${success} succeeded runs, ${runs.length - success - failed} other runs`}
          parts={[
            { n: failed, cls: 'bg-bad' },
            { n: success, cls: 'bg-sla-ok' },
            { n: runs.length - success - failed, cls: 'bg-skip-line' },
          ]}
        />
        <Legend cls="bg-bad">{failed} failed runs</Legend>
        <Legend cls="bg-sla-ok">{success} succeeded runs</Legend>
        <Legend cls="bg-skip-line">{unknown} jobs unknown</Legend>
      </div>
      <div className="flex shrink-0 flex-col gap-3 border-b border-line px-4 py-3 lg:hidden">
        <div className="flex gap-2">
          {searchBox('job-search-mobile', 'h-10 flex-1')}
          <button
            type="button"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((v) => !v)}
            className="flex h-10 items-center gap-2 rounded-lg border border-border px-3 text-[13.5px]"
          >
            <SlidersHorizontalIcon className="size-3.5" /> Filters
          </button>
        </div>
        {filtersOpen ? (
          <div className="grid gap-4 sm:grid-cols-2">{facets}</div>
        ) : null}
      </div>
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label="Filters"
          className="hidden w-[216px] shrink-0 flex-col gap-[18px] overflow-y-auto border-r border-line px-2.5 py-3.5 lg:flex"
        >
          {facets}
        </aside>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-5 overflow-y-auto px-4 py-4 md:hidden">
          <Card className="gap-2.5 p-3.5">
            <div className="text-[14.5px] font-medium">
              {jobs.length} jobs ·{' '}
              <span className="text-bad-text">
                {attention.length} need attention
              </span>
            </div>
            <div className="text-[12.5px] text-muted-foreground">
              Latest run states from at most 100 records, not complete job
              histories.
            </div>
          </Card>
          <MobileSection
            title={`Needs attention · ${attention.length}`}
            jobs={attention}
            env={env}
          />
          {grouped.map((group) => (
            <MobileSection
              key={group.name}
              title={`${group.name} · ${group.jobs.length}`}
              jobs={group.jobs}
              env={env}
            />
          ))}
        </div>
        <section
          aria-label="Jobs"
          className="hidden min-h-0 min-w-0 flex-1 flex-col overflow-y-auto md:flex"
        >
          <div
            className={cn(
              rowGrid,
              'sticky top-0 z-10 h-8 border-b border-line-soft bg-raised text-xs text-muted-foreground',
            )}
          >
            <span />
            <span>Job</span>
            <span>Repository</span>
            <span>Why it's here</span>
            <span>Last 24 observed</span>
            <span className="text-right">Last run</span>
            <span>Owner</span>
          </div>
          {attention.length ? (
            <>
              <div className="flex items-center gap-2.5 px-4 pt-2.5 pb-2">
                <h2 className="text-[13.5px] font-medium">Needs attention</h2>
                <span className="text-[13px] text-muted-foreground">
                  {attention.length} jobs, pinned to the top
                </span>
              </div>
              {attention.map((job) => (
                <JobRow key={job.id} job={job} env={env} />
              ))}
            </>
          ) : null}
          {grouped.length ? (
            <>
              <div className="flex items-center gap-2.5 px-4 pt-3.5 pb-1.5">
                <h2 className="text-[13.5px] font-medium">
                  Other observed jobs
                </h2>
                <span className="text-[13px] text-muted-foreground">
                  Grouped by native repository; success is not inferred for
                  missing history
                </span>
              </div>
              {grouped.map((group) => {
                const expanded = open.has(group.name)
                return (
                  <div key={group.name}>
                    <button
                      type="button"
                      aria-expanded={expanded}
                      onClick={() => toggleGroup(group.name)}
                      className={cn(
                        rowGrid,
                        'h-[38px] w-full border-b border-line-soft text-left text-[13.5px] hover:bg-raised',
                        expanded && 'bg-raised',
                      )}
                    >
                      <ChevronRightIcon
                        className={cn(
                          'size-3 text-faint transition-transform',
                          expanded && 'rotate-90',
                        )}
                      />
                      <span className="truncate font-medium">{group.name}</span>
                      <span className="text-muted-foreground">
                        {group.jobs.length} jobs
                      </span>
                      <span className="truncate text-muted-foreground">
                        Status from latest observed run
                      </span>
                      <GroupRuns jobs={group.jobs} />
                      <span className="text-right font-mono text-xs text-muted-foreground">
                        Unknown
                      </span>
                      <span className="text-muted-foreground">Unknown</span>
                    </button>
                    {expanded ? (
                      <div className="bg-sunken">
                        {group.jobs.map((job) => (
                          <JobRow key={job.id} job={job} env={env} />
                        ))}
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </>
          ) : null}
          {!visible.length ? (
            <div className="p-6 text-sm text-muted-foreground">
              No jobs match these filters.
            </div>
          ) : null}
          <div className="mt-auto flex min-h-11 items-center border-t border-line px-4 text-[13px] text-muted-foreground">
            Showing {visible.length} of {jobs.length} · API returns at most 100
            recent environment-scoped runs
          </div>
        </section>
      </div>
    </>
  )
}

function JobRow({
  job,
  env,
}: {
  job: ObservedJob
  env: Awaited<ReturnType<typeof getPipelineList>>['env']
}) {
  return (
    <div
      className={cn(
        rowGrid,
        'min-h-[34px] border-b border-line-soft text-[13px] hover:bg-raised',
      )}
    >
      <Dot tone={runStates[observedRunState(job.latest)].tone} />
      <Link
        to="/pipelines/$jobName"
        params={{ jobName: job.id }}
        search={{ env }}
        className="truncate font-mono text-[12.5px] text-foreground"
      >
        {job.id}
      </Link>
      <span className="truncate text-muted-foreground">
        {job.repository_name}
      </span>
      <span
        className={
          job.failing
            ? 'truncate text-bad-text'
            : 'truncate text-muted-foreground'
        }
      >
        {job.latest
          ? `Latest observed: ${job.latest.status}`
          : 'No run observed — status unknown'}
      </span>
      <RunCells job={job} env={env} />
      <span className="text-right text-xs text-muted-foreground">
        {job.latest?.created_at ?? 'Unknown'}
      </span>
      <span className="text-muted-foreground">Unknown</span>
    </div>
  )
}
function RunCells({
  job,
  env,
}: {
  job: ObservedJob
  env: Awaited<ReturnType<typeof getPipelineList>>['env']
}) {
  return (
    <span className="flex gap-0.5">
      {job.runs.toReversed().map((run) => (
        <Link
          key={run.run_id}
          to="/pipelines/$jobName"
          params={{ jobName: job.id }}
          search={{ env, run: run.run_id }}
          title={`${run.status} · ${run.created_at}`}
          className={cn('h-[22px] w-2 rounded-[2px]', runColor(run.status))}
        />
      ))}
      {!job.runs.length ? (
        <span className="text-xs text-muted-foreground">Not observed</span>
      ) : null}
    </span>
  )
}
function GroupRuns({ jobs }: { jobs: Array<ObservedJob> }) {
  const latest = jobs.flatMap((j) => j.runs.slice(0, 4)).slice(0, 16)
  return (
    <span className="flex gap-0.5">
      {latest.map((run) => (
        <span
          key={run.run_id}
          className={cn('h-2 w-2 rounded-[2px]', runColor(run.status))}
        />
      ))}
    </span>
  )
}
function MobileSection({
  title,
  jobs,
  env,
}: {
  title: string
  jobs: Array<ObservedJob>
  env: Awaited<ReturnType<typeof getPipelineList>>['env']
}) {
  if (!jobs.length) return null
  return (
    <section className="flex flex-col gap-2">
      <Eyebrow>{title}</Eyebrow>
      <Card className="overflow-hidden">
        {jobs.map((job) => (
          <Link
            key={job.id}
            to="/pipelines/$jobName"
            params={{ jobName: job.id }}
            search={{ env }}
            className="flex items-start gap-2.5 border-b border-line-soft px-3.5 py-3 text-foreground last:border-0"
          >
            <Dot
              tone={runStates[observedRunState(job.latest)].tone}
              className="mt-1.5"
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-[13px]">
                {job.id}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                {job.repository_name} · {job.latest?.status ?? 'Unknown'}
              </span>
            </span>
          </Link>
        ))}
      </Card>
    </section>
  )
}
function FacetItem({
  checked,
  onChange,
  count,
  children,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  count: number
  children: React.ReactNode
}) {
  return (
    <label className="flex h-7 items-center gap-2 rounded-md px-1.5 text-[13px] text-text-2 hover:bg-soft">
      <Checkbox
        checked={checked}
        onCheckedChange={(value) => onChange(value === true)}
        className="size-3.5"
      />
      {children}
      <span className="ml-auto font-mono text-[11.5px] text-muted-foreground">
        {count}
      </span>
    </label>
  )
}
function DisabledFacet({ label }: { label: string }) {
  return (
    <label className="flex min-h-7 items-center gap-2 px-1.5 text-[12px] text-muted-foreground opacity-70">
      <Checkbox disabled className="size-3.5" />
      {label}
    </label>
  )
}
function Legend({ cls, children }: { cls: string; children: React.ReactNode }) {
  return (
    <span className="flex items-center gap-1.5 text-[13px] whitespace-nowrap">
      <span className={cn('size-2 rounded-[2px]', cls)} />
      {children}
    </span>
  )
}
