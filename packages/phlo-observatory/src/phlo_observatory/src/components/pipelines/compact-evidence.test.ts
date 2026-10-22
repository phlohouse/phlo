/** Checks compact diagnostics never infer skipped steps or zero durations. */
import { describe, expect, it } from 'vitest'
import {
  averageRunDuration,
  observedRunSteps,
  recentPipelineRuns,
} from './compact-evidence'
import type { ApiRun, getRunLogPage } from '@/lib/data/api/pipelines'

const run = (
  id: string,
  job: string,
  created: string,
  duration: number | null,
): ApiRun => ({
  run_id: id,
  job_id: job,
  created_at: created,
  status: 'SUCCESS',
  duration_seconds: duration,
  started_at: null,
  ended_at: null,
  selected_assets: [],
})
const event = (
  event_type: string,
  step_key: string,
  timestamp: string,
): Awaited<ReturnType<typeof getRunLogPage>>['items'][number] => ({
  event_type,
  step_key,
  timestamp,
  message: '',
  level: 'INFO',
  error: null,
  captured_file_key: null,
  captured_step_keys: [],
})

describe('compact pipeline evidence', () => {
  it('keeps history scoped to the job, newest first, with at most24 real runs', () => {
    const runs = Array.from({ length: 30 }, (_, index) =>
      run(String(index), 'one', new Date(index * 1000).toISOString(), index),
    )
    expect(
      recentPipelineRuns(
        [...runs, run('foreign', 'two', '2026-10-22', 1)],
        'one',
      ).map((item) => item.run_id),
    ).toEqual(Array.from({ length: 24 }, (_, index) => String(29 - index)))
  })
  it('excludes missing duration evidence from the average', () => {
    expect(averageRunDuration([run('a', 'one', '', null)])).toBeNull()
    expect(
      averageRunDuration([
        run('a', 'one', '', 0),
        run('b', 'one', '', 10),
        run('c', 'one', '', null),
      ]),
    ).toBe(5)
  })
  it('derives durations from matching recorded step events and preserves explicit skips', () => {
    const steps = observedRunSteps([
      event('STEP_START', 'extract', '2026-10-22T10:00:00Z'),
      event('STEP_SUCCESS', 'extract', '2026-10-22T10:00:03Z'),
      event('STEP_FAILURE', 'normalize', '2026-10-22T10:00:04Z'),
      event('STEP_SKIPPED', 'load', '2026-10-22T10:00:04Z'),
      event('LOGS_CAPTURED', 'not-a-step', '2026-10-22T10:00:04Z'),
    ])
    expect(
      steps.map(({ name, status, duration }) => ({ name, status, duration })),
    ).toEqual([
      { name: 'extract', status: 'Succeeded', duration: 3 },
      { name: 'normalize', status: 'Failed', duration: null },
      { name: 'load', status: 'Skipped', duration: null },
    ])
  })
  it('does not retain a previous attempt duration when a step restarts', () => {
    const steps = observedRunSteps([
      event('STEP_START', 'extract', '2026-10-22T10:00:00Z'),
      event('STEP_FAILURE', 'extract', '2026-10-22T10:00:03Z'),
      event('STEP_RESTARTED', 'extract', '2026-10-22T10:00:04Z'),
    ])
    expect(steps[0]).toMatchObject({ status: 'Running', duration: null })
  })
})
