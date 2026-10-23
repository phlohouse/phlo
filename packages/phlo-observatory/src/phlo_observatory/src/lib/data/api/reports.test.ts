/** Report links require the complete producer-recorded identity, never a provider ID fallback. */
import { expect, it } from 'vitest'
import { runReportIdentity } from './reports'

it('links the exact logical run and attempt recorded by the producer', () => {
  expect(
    runReportIdentity({
      'phlo/project_id': 'project-alpha',
      'phlo/run_id': 'logical-run',
      'phlo/attempt': '3',
      'dagster/schedule_name': 'daily',
    }),
  ).toEqual({ project: 'project-alpha', run: 'logical-run', attempt: 3 })
})

it('does not guess missing or invalid report identity', () => {
  const tags = {
    'phlo/project_id': 'project-alpha',
    'phlo/run_id': 'logical-run',
    'phlo/attempt': '3',
  }
  expect(runReportIdentity(undefined)).toBeNull()
  expect(runReportIdentity({ 'phlo/run_id': 'provider-run' })).toBeNull()
  for (const attempt of ['', '0', '-1', '2.5', '1e2', '9007199254740993']) {
    expect(runReportIdentity({ ...tags, 'phlo/attempt': attempt })).toBeNull()
  }
  expect(runReportIdentity({ ...tags, 'phlo/project_id': '' })).toBeNull()
})
