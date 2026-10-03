/** Tests scoped pipeline history, source correlations and maintenance windows. */
import { expect, it } from 'vitest'
import {
  correlatedFailures,
  overlappingMaintenance,
  pipelineGroupName,
  pipelineMatches,
} from './evidence'
import { pipelineSearchSchema } from './bits'
import type { ApiRun } from '@/lib/data/api/pipelines'
import { collectRunPages } from '@/lib/data/api/pipelines'

function run(id: string, job: string, minute = 0): ApiRun {
  return {
    run_id: id,
    job_id: job,
    status: 'FAILURE',
    created_at: new Date(minute * 60_000).toISOString(),
    started_at: null,
    ended_at: null,
    duration_seconds: null,
    selected_assets: [],
  }
}
const jobs = ['one', 'two', 'unknown', 'different'].map((id) => ({
  id,
  description: null,
  repository_name: 'not-a-source',
  domain: 'telemetry',
  owners: [],
  selected_assets: [],
  feeds_batch_release: false,
  source:
    id === 'unknown' ? null : id === 'different' ? 'other' : 'declared-source',
}))

it('collects beyond 100 rows and continues after an empty scoped page', async () => {
  const requested: Array<string | null> = []
  const result = await collectRunPages((cursor) => {
    requested.push(cursor)
    return Promise.resolve({
      env: 'prod',
      items:
        cursor === null
          ? []
          : Array.from({ length: cursor === 'first' ? 100 : 37 }, (_, index) =>
              run(`${cursor}-${index}`, 'one'),
            ),
      next_cursor:
        cursor === null ? 'first' : cursor === 'first' ? 'last' : null,
    })
  })
  expect(requested).toEqual([null, 'first', 'last'])
  expect(result).toHaveLength(137)
  expect(result.at(-1)?.run_id).toBe('last-36')
  await expect(
    collectRunPages(() =>
      Promise.resolve({
        env: 'prod',
        items: [],
        next_cursor: 'stuck',
      }),
    ),
  ).rejects.toThrow('cursor did not advance')
})

it('correlates declared source and distinct failed jobs inside a fixed 10-minute window', () => {
  const result = correlatedFailures(jobs, [
    run('a', 'one'),
    run('b', 'one', 2),
    run('c', 'two', 10),
    run('d', 'two', 10.01),
    run('e', 'unknown', 1),
    run('f', 'different', 1),
  ])
  expect(result.map((group) => group.runs.map((item) => item.run_id))).toEqual([
    ['a', 'b', 'c'],
  ])
  expect(
    correlatedFailures(jobs, [run('a', 'one'), run('b', 'one', 2)]),
  ).toEqual([])
})

it('annotates actual policy intervals, excluding touching but non-overlapping boundaries', () => {
  const windows = [
    {
      id: 'policy',
      starts_at: new Date(60_000).toISOString(),
      ends_at: new Date(180_000).toISOString(),
      description: 'Upgrade',
    },
  ]
  expect(overlappingMaintenance(windows, 0, 60_000)).toEqual([])
  expect(overlappingMaintenance(windows, 60_000, 90_000)).toEqual(windows)
  expect(overlappingMaintenance(windows, 180_000, 240_000)).toEqual([])
})

it('combines retained predicates using declared owner, source, release and latest state', () => {
  const job = {
    ...jobs[0],
    owners: ['fleet-operations'],
    feeds_batch_release: true,
  }
  const identity = {
    subject: 'fleet-operations',
    email: 'operator@example.test',
  }
  const search = pipelineSearchSchema.parse({
    q: ' ONE ',
    by: 'owner',
    owners: ['fleet-operations'],
    sources: ['declared-source'],
    states: ['failed'],
    saved: ['mine', 'release'],
  })
  expect(pipelineMatches(job, run('a', 'one'), search, identity)).toBe(true)
  expect(pipelineGroupName(job, search.by)).toBe('fleet-operations')
  expect(
    pipelineMatches(job, run('a', 'one'), search, {
      subject: 'another',
      email: null,
    }),
  ).toBe(false)
  expect(
    pipelineMatches(
      { ...job, feeds_batch_release: false },
      run('a', 'one'),
      search,
      identity,
    ),
  ).toBe(false)
  expect(
    pipelineMatches(
      { ...job, source: 'different' },
      run('a', 'one'),
      search,
      identity,
    ),
  ).toBe(false)
  expect(
    pipelineMatches(
      job,
      { ...run('a', 'one'), status: 'SUCCESS' },
      search,
      identity,
    ),
  ).toBe(false)
  const unknown = { ...jobs[2], domain: null }
  expect(
    pipelineMatches(
      unknown,
      undefined,
      pipelineSearchSchema.parse({
        owners: [''],
        sources: [''],
        states: ['unknown'],
      }),
      identity,
    ),
  ).toBe(true)
  expect(pipelineGroupName(unknown, 'domain')).toBe('Unknown domain')
})
