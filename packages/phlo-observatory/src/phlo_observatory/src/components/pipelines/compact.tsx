/** Compact job history and inline diagnostics using environment-scoped observations. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowUpRightIcon, XIcon } from 'lucide-react'
import {
  averageRunDuration,
  observedRunSteps,
  recentPipelineRuns,
} from './compact-evidence'
import { runBadge, runColor, runDuration } from './bits'
import type { ApiRun, getPipelineList } from '@/lib/data/api/pipelines'
import { getRunLogPage } from '@/lib/data/api/pipelines'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type Data = Awaited<ReturnType<typeof getPipelineList>>
type Job = Data['jobs'][number]
type Selection = { job: Job; run: ApiRun | null }
type LogPage = Awaited<ReturnType<typeof getRunLogPage>>

export function CompactPipelines({
  jobs,
  runs,
  env,
}: Pick<Data, 'jobs' | 'runs' | 'env'>) {
  const [selection, setSelection] = React.useState<Selection | null>(() => {
    const job =
      jobs.find(
        (item) => recentPipelineRuns(runs, item.id)[0]?.status === 'FAILURE',
      ) ?? jobs[0]
    return job
      ? { job, run: recentPipelineRuns(runs, job.id)[0] ?? null }
      : null
  })
  const selected =
    selection && jobs.some((job) => job.id === selection.job.id)
      ? selection
      : null
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto xl:flex-row xl:overflow-hidden">
      <section
        aria-label="Pipeline run history"
        className="min-h-0 min-w-0 flex-1 xl:overflow-y-auto"
      >
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(100px,1fr)_64px] sm:grid-cols-[minmax(180px,1fr)_minmax(150px,1fr)_90px] gap-3 border-b border-line bg-raised px-5 py-2 text-xs text-muted-foreground">
          <span>Job</span>
          <span>Last 24 observed runs</span>
          <span className="text-right">Average</span>
        </div>
        {jobs.map((job) => {
          const history = recentPipelineRuns(runs, job.id)
          const latest = history[0]
          return (
            <div
              key={job.id}
              className={cn(
                'grid grid-cols-[minmax(0,1fr)_minmax(100px,1fr)_64px] sm:grid-cols-[minmax(180px,1fr)_minmax(150px,1fr)_90px] items-center gap-3 border-b border-line-soft px-5 py-3',
                selected?.job.id === job.id && 'bg-primary-soft',
              )}
            >
              <div className="min-w-0">
                <button
                  type="button"
                  className="flex max-w-full items-center gap-2 text-left text-[13px] text-foreground outline-none focus-visible:rounded focus-visible:ring-2 focus-visible:ring-ring"
                  aria-pressed={selected?.job.id === job.id}
                  aria-label={`Inspect ${job.id}`}
                  onClick={() => setSelection({ job, run: latest ?? null })}
                >
                  <span
                    aria-hidden
                    className={cn(
                      'size-2 shrink-0 rounded-full',
                      latest ? runColor(latest.status) : 'bg-skip-line',
                    )}
                  />
                  <span className="truncate font-mono">{job.id}</span>
                </button>
                <p className="mb-0 mt-1 pl-4 text-xs text-muted-foreground">
                  {job.source ?? 'Source not declared'} ·{' '}
                  {latest?.status ?? 'No runs observed'}
                </p>
              </div>
              <div
                className="flex min-w-0 flex-wrap gap-0.5"
                aria-label={`Recent runs for ${job.id}`}
              >
                {history.toReversed().map((run) => (
                  <button
                    key={run.run_id}
                    type="button"
                    aria-label={`${job.id} ${run.status} run ${run.run_id}`}
                    title={`${run.status} · ${run.created_at}`}
                    aria-pressed={selected?.run?.run_id === run.run_id}
                    onClick={() => setSelection({ job, run })}
                    className={cn(
                      'h-7 w-5 sm:w-3 shrink-0 rounded-[2px] outline-none hover:brightness-125 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                      runColor(run.status),
                      selected?.run?.run_id === run.run_id &&
                        'ring-1 ring-foreground ring-offset-2 ring-offset-background',
                    )}
                  />
                ))}
                {!history.length ? (
                  <span className="text-xs text-muted-foreground">
                    No runs observed
                  </span>
                ) : null}
              </div>
              <span className="text-right font-mono text-xs text-muted-foreground">
                {runDuration(averageRunDuration(history))}
              </span>
            </div>
          )
        })}
        {!jobs.length ? (
          <p className="p-5 text-sm text-muted-foreground">
            No jobs match these filters.
          </p>
        ) : null}
        <div className="flex flex-wrap gap-4 px-5 py-4 text-xs text-muted-foreground">
          {[
            ['Succeeded', 'bg-sla-ok'],
            ['Failed', 'bg-bad'],
            ['Running / queued', 'bg-warn-bar'],
            ['Canceled', 'bg-skip-line'],
          ].map(([label, color]) => (
            <span key={label} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className={cn('h-3 w-2 rounded-[2px]', color)}
              />
              {label}
            </span>
          ))}
        </div>
      </section>
      {selected ? (
        <RunInspector
          key={`${env}:${selected.job.id}:${selected.run?.run_id}`}
          selection={selected}
          env={env}
          close={() => setSelection(null)}
        />
      ) : null}
    </div>
  )
}

function useRunDiagnostics(env: Data['env'], selection: Selection) {
  const [page, setPage] = React.useState<LogPage>()
  const [error, setError] = React.useState<string>()
  const [pending, setPending] = React.useState(Boolean(selection.run))
  const run = selection.run
  React.useEffect(() => {
    if (!run) return
    let active = true
    getRunLogPage({
      data: { env, id: selection.job.id, run: run.run_id },
    }).then(
      (next) => {
        if (active) {
          setPage(next)
          setPending(false)
        }
      },
      (caught) => {
        if (active) {
          setError(
            caught instanceof Error
              ? caught.message
              : 'Could not load run diagnostics.',
          )
          setPending(false)
        }
      },
    )
    return () => {
      active = false
    }
  }, [env, selection.job.id, run?.run_id])
  const more = async () => {
    if (!run || !page?.next_cursor || pending) return
    setPending(true)
    setError(undefined)
    try {
      const next = await getRunLogPage({
        data: {
          env,
          id: selection.job.id,
          run: run.run_id,
          cursor: page.next_cursor,
        },
      })
      setPage({ ...next, items: [...page.items, ...next.items] })
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'Could not load more events.',
      )
    } finally {
      setPending(false)
    }
  }
  return { page, error, pending, more }
}

function RunInspector({
  selection,
  env,
  close,
}: {
  selection: Selection
  env: Data['env']
  close: () => void
}) {
  const { job, run } = selection
  const { page, error, pending, more } = useRunDiagnostics(env, selection)
  const steps = observedRunSteps(page?.items ?? [])
  const failures = (page?.items ?? []).filter((event) => event.error !== null)
  return (
    <aside
      aria-label={`Run diagnostics for ${job.id}`}
      className="flex shrink-0 flex-col border-t border-line xl:w-[400px] xl:overflow-y-auto xl:border-t-0 xl:border-l"
    >
      <RunSummary selection={selection} close={close} />
      <StepDiagnostics
        steps={steps}
        pending={pending}
        truncated={page?.truncated ?? false}
        more={more}
      />
      {failures.length ? (
        <div className="border-t border-line px-5 py-4">
          <h3 className="mb-3 mt-0 text-xs font-medium text-muted-foreground">
            Error
          </h3>
          {failures.slice(0, 3).map((event, index) => (
            <pre
              key={index}
              className="m-0 mb-3 overflow-auto rounded-lg border border-bad bg-bad-soft p-3 font-mono text-xs whitespace-pre-wrap break-words text-bad-text"
            >
              {formatError(event.error!)}
            </pre>
          ))}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="mx-5 text-sm text-bad-text">
          {error}
        </p>
      ) : null}
      <div className="mt-auto flex flex-wrap gap-2 border-t border-line px-5 py-4">
        <Button
          variant="outline"
          render={
            <Link
              to="/pipelines/$jobName"
              params={{ jobName: job.id }}
              search={{ env }}
            />
          }
        >
          Job details <ArrowUpRightIcon />
        </Button>
        {run ? (
          <Button
            variant="outline"
            render={
              <Link
                to="/pipelines/$jobName/runs/$runId"
                params={{ jobName: job.id, runId: run.run_id }}
                search={{ env }}
              />
            }
          >
            View run and logs
          </Button>
        ) : null}
      </div>
    </aside>
  )
}

function RunSummary({
  selection,
  close,
}: {
  selection: Selection
  close: () => void
}) {
  const { job, run } = selection
  return (
    <>
      <div className="flex items-center gap-2 px-5 pt-5">
        <Badge variant={run ? runBadge(run.status) : 'neutral'}>
          {run?.status ?? 'No run'}
        </Badge>
        <span
          className="min-w-0 truncate font-mono text-xs text-muted-foreground"
          title={run?.run_id}
        >
          {run?.run_id.slice(0, 8)}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          aria-label="Close run diagnostics"
          onClick={close}
        >
          <XIcon />
        </Button>
      </div>
      <h2 className="mx-5 my-3 break-words font-mono text-base font-medium">
        {job.id}
      </h2>
      <dl className="mx-5 mb-5 grid grid-cols-[80px_minmax(0,1fr)] gap-x-4 gap-y-2 text-[13px]">
        <dt className="text-muted-foreground">Duration</dt>
        <dd className="m-0">{runDuration(run?.duration_seconds ?? null)}</dd>
        <dt className="text-muted-foreground">Started</dt>
        <dd className="m-0">
          {run?.started_at
            ? new Date(run.started_at).toLocaleString('en-GB', {
                timeZone: 'UTC',
              }) + ' UTC'
            : 'Not observed'}
        </dd>
        <dt className="text-muted-foreground">Target</dt>
        <dd className="m-0 break-words font-mono text-xs">
          {(run?.selected_assets.length
            ? run.selected_assets
            : job.selected_assets
          )
            .map((path) => path.join('.'))
            .join(', ') || 'Not declared'}
        </dd>
      </dl>
    </>
  )
}

function StepDiagnostics({
  steps,
  pending,
  truncated,
  more,
}: {
  steps: ReturnType<typeof observedRunSteps>
  pending: boolean
  truncated: boolean
  more: () => Promise<void>
}) {
  const maximum = Math.max(1, ...steps.map((step) => step.duration ?? 0))
  return (
    <div className="border-t border-line px-5 py-4">
      <h3 className="mb-4 mt-0 text-xs font-medium text-muted-foreground">
        Steps
      </h3>
      <div className="grid gap-3">
        {steps.map((step) => (
          <div
            key={step.name}
            className="grid grid-cols-[minmax(0,1fr)_minmax(60px,1fr)_70px] items-center gap-3 text-xs"
          >
            <span className="truncate font-mono" title={step.name}>
              {step.name}
            </span>
            <span
              className="h-2 overflow-hidden rounded-sm bg-soft"
              aria-label={`${step.name}: ${step.status}`}
            >
              <span
                className={cn(
                  'block h-full',
                  step.status === 'Failed'
                    ? 'bg-bad'
                    : step.status === 'Succeeded'
                      ? 'bg-sla-ok'
                      : 'bg-skip-line',
                )}
                style={{
                  width:
                    step.duration === null
                      ? '0%'
                      : `${(100 * step.duration) / maximum}%`,
                }}
              />
            </span>
            <span
              className={cn(
                'text-right',
                step.status === 'Failed'
                  ? 'text-bad-text'
                  : 'text-muted-foreground',
              )}
            >
              {step.duration === null
                ? step.status
                : runDuration(step.duration)}
            </span>
          </div>
        ))}
      </div>
      {!steps.length ? (
        <p className="m-0 text-xs text-muted-foreground">
          {pending
            ? 'Loading recorded step events…'
            : 'No step events in the loaded diagnostics.'}
        </p>
      ) : null}
      {truncated ? (
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          disabled={pending}
          onClick={() => void more()}
        >
          {pending ? 'Loading…' : 'Load more events'}
        </Button>
      ) : null}
    </div>
  )
}

function formatError(
  error: NonNullable<LogPage['items'][number]['error']>,
): string {
  return [
    `${error.class_name ?? 'Error'}: ${error.message}`,
    ...error.causes.map(formatError),
  ].join('\n\nCaused by ')
}
