/** Derives compact run diagnostics from recorded Dagster events only. */
import type { ApiRun, getRunLogPage } from '@/lib/data/api/pipelines'

type Events = Awaited<ReturnType<typeof getRunLogPage>>['items']
export function recentPipelineRuns(runs: Array<ApiRun>, jobId: string) {
  return runs
    .filter((run) => run.job_id === jobId)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, 24)
}
export function averageRunDuration(runs: Array<ApiRun>) {
  const durations = runs.flatMap((run) =>
    run.duration_seconds === null ? [] : [run.duration_seconds],
  )
  return durations.length
    ? durations.reduce((sum, value) => sum + value, 0) / durations.length
    : null
}
export function observedRunSteps(events: Events) {
  const steps = new Map<
    string,
    { name: string; status: string; started?: number; ended?: number }
  >()
  for (const event of events) {
    if (
      !event.step_key ||
      ![
        'STEP_START',
        'STEP_SUCCESS',
        'STEP_FAILURE',
        'STEP_SKIPPED',
        'STEP_RESTARTED',
        'STEP_UP_FOR_RETRY',
      ].includes(event.event_type)
    )
      continue
    const step = steps.get(event.step_key) ?? {
      name: event.step_key,
      status: 'Observed',
    }
    const timestamp = Date.parse(event.timestamp)
    if (
      event.event_type === 'STEP_START' ||
      event.event_type === 'STEP_RESTARTED'
    ) {
      if (Number.isFinite(timestamp)) step.started = timestamp
      step.ended = undefined
      step.status = 'Running'
    } else if (event.event_type === 'STEP_SKIPPED') step.status = 'Skipped'
    else if (event.event_type === 'STEP_UP_FOR_RETRY') step.status = 'Retrying'
    else {
      step.status = event.event_type === 'STEP_FAILURE' ? 'Failed' : 'Succeeded'
      if (Number.isFinite(timestamp)) step.ended = timestamp
    }
    steps.set(event.step_key, step)
  }
  return [...steps.values()].map((step) => ({
    ...step,
    duration:
      step.started !== undefined &&
      step.ended !== undefined &&
      step.ended >= step.started
        ? (step.ended - step.started) / 1000
        : null,
  }))
}
