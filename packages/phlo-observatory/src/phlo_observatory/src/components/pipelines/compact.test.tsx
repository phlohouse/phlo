// @vitest-environment jsdom
/** Exercises selection and failure diagnostics without leaving the pipeline list. */
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CompactPipelines } from './compact'
import type { ReactNode } from 'react'
import type { ApiRun, getPipelineList } from '@/lib/data/api/pipelines'

const readLogs = vi.hoisted(() => vi.fn())
vi.mock('@/lib/data/api/pipelines', () => ({ getRunLogPage: readLogs }))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
const jobs: Awaited<ReturnType<typeof getPipelineList>>['jobs'] = [
  'ingest',
  'audit',
].map((id) => ({
  id,
  repository_name: 'repo',
  description: null,
  domain: null,
  owners: [],
  source: null,
  feeds_batch_release: false,
  selected_assets: [],
}))
const runs: Array<ApiRun> = [
  {
    run_id: 'failure-one',
    job_id: 'ingest',
    status: 'FAILURE',
    created_at: '2026-10-22T10:00:00Z',
    started_at: null,
    ended_at: null,
    duration_seconds: null,
    selected_assets: [],
  },
  {
    run_id: 'success-two',
    job_id: 'audit',
    status: 'SUCCESS',
    created_at: '2026-10-22T10:01:00Z',
    started_at: null,
    ended_at: null,
    duration_seconds: 3,
    selected_assets: [],
  },
]
const page = (run_id: string) => ({
  env: 'sandbox',
  run_id,
  items: [],
  next_cursor: null,
  truncated: false,
})

describe('compact pipelines', () => {
  it('opens actual failed-run error evidence and selects another run with the keyboard', async () => {
    readLogs.mockImplementation(({ data }: { data: { run: string } }) =>
      Promise.resolve(
        data.run === 'failure-one'
          ? {
              ...page(data.run),
              items: [
                {
                  event_type: 'STEP_FAILURE',
                  step_key: 'normalize',
                  timestamp: '2026-10-22T10:00:01Z',
                  error: {
                    class_name: 'ValidationError',
                    message: 'Unexpected do_sat_pct column',
                    stack: [],
                    causes: [],
                  },
                },
              ],
            }
          : page(data.run),
      ),
    )
    const user = userEvent.setup()
    render(<CompactPipelines jobs={jobs} runs={runs} env="sandbox" />)
    const diagnostics = screen.getByRole('complementary', {
      name: 'Run diagnostics for ingest',
    })
    expect(
      await within(diagnostics).findByText(/Unexpected do_sat_pct/),
    ).toBeTruthy()
    const other = screen.getByRole('button', {
      name: 'audit SUCCESS run success-two',
    })
    other.focus()
    await user.keyboard('{Enter}')
    expect(
      await screen.findByRole('complementary', {
        name: 'Run diagnostics for audit',
      }),
    ).toBeTruthy()
    expect(readLogs).toHaveBeenLastCalledWith({
      data: { env: 'sandbox', id: 'audit', run: 'success-two' },
    })
    expect(screen.getByRole('link', { name: 'View run and logs' })).toBeTruthy()
  })
  it('shows provider errors and closes the inspector without a navigation', async () => {
    readLogs.mockRejectedValue(new Error('Run history denied'))
    const user = userEvent.setup()
    render(<CompactPipelines jobs={jobs} runs={runs} env="sandbox" />)
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Run history denied',
    )
    await user.click(
      screen.getByRole('button', { name: 'Close run diagnostics' }),
    )
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(
      screen.getByRole('region', { name: 'Pipeline run history' }),
    ).toBeTruthy()
  })
})
