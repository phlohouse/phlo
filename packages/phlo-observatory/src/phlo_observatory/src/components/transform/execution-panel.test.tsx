// @vitest-environment jsdom
/** Candidate execution submits a pinned revision and retains actual run/output identities. */
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { CandidateExecutionPanel } from './execution-panel'
import type {
  TransformCompilation,
  TransformDraft,
  TransformExecution,
} from '@/lib/data/api/transform-editor'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  run: vi.fn(),
  branches: vi.fn(),
}))
vi.mock('@/lib/data/api/transform-editor', () => ({
  getTransformExecutions: api.list,
  executeTransformDraft: api.run,
}))
vi.mock('@/lib/data/api/branches', () => ({ getBranchesPage: api.branches }))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
const draft: TransformDraft = {
  env: 'sandbox',
  ref: 'main',
  model: 'review.inspection',
  path: 'inspection.sql',
  sql: 'SELECT batch_id FROM gold.batches',
  draft_id: '31947c63-2dfa-4da3-88be-6d2f791656ec',
  revision: 3,
  revision_digest: 'c'.repeat(64),
  source_digest: 'a'.repeat(64),
  workspace_digest: 'b'.repeat(64),
  sql_digest: 'd'.repeat(64),
  created_at: '',
}
const compilation: TransformCompilation = {
  env: 'sandbox',
  draft_id: draft.draft_id,
  revision: 3,
  revision_digest: draft.revision_digest,
  ref: 'main',
  status: 'ready',
  diagnostics: [],
  plan_id: 'real-plan',
  compiled_sql: draft.sql,
  target: 'preview_sandbox.review.inspection',
  sql_hash: 'sql',
  desired_version: 'desired',
  materialization_available: false,
  source: 'phlo-transform-engine',
}
const result: TransformExecution = {
  execution_id: 'actual-execution',
  model: draft.model,
  env: 'sandbox',
  ref: 'main',
  draft_id: draft.draft_id,
  revision: 3,
  revision_digest: draft.revision_digest,
  status: 'passed',
  created_at: '',
  candidate_ref: 'sandbox-transform-actual',
  candidate_catalog: 'iceberg_candidate_actual',
  candidate_binding: {},
  candidate_result_hash: 'f'.repeat(64),
  candidate_snapshot_id: '9223372036854775807',
  report: {
    run_id: 'actual-native-run',
    plan_id: 'actual-native-plan',
    models: [],
  },
  preview: {
    columns: ['batch_id'],
    rows: [{ batch_id: 'B01' }],
    has_more: false,
    row_count: 1,
    snapshot_id: '9223372036854775807',
  },
}
it.each([true, false])(
  'renews the request key only after proven pre-write rejection (%s)',
  async (rejected) => {
    api.list.mockResolvedValue({ items: [] })
    api.branches.mockResolvedValue({
      branches: [{ name: 'main', hash: 'e'.repeat(64) }],
    })
    if (rejected)
      api.run.mockResolvedValueOnce({
        rejected_before_write: true,
        message: 'Compile unavailable',
      })
    else api.run.mockRejectedValueOnce(new Error('Transport unavailable'))
    api.run.mockResolvedValueOnce(result)
    render(
      <CandidateExecutionPanel
        env="sandbox"
        model={draft.model}
        draft={draft}
        dirty={false}
        compilation={compilation}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Run candidate' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'Run candidate' }))
    await screen.findByText(/Native run actual-native-run/)
    const first = api.run.mock.calls[0][0].data.idempotency_key
    const second = api.run.mock.calls[1][0].data.idempotency_key
    if (rejected) expect(second).not.toBe(first)
    else expect(second).toBe(first)
  },
)
it('requires a matching compiled revision and preserves the exact Iceberg snapshot identity', async () => {
  let finishHistory!: (value: { items: Array<TransformExecution> }) => void
  api.list.mockReturnValue(
    new Promise((resolve) => {
      finishHistory = resolve
    }),
  )
  api.branches.mockResolvedValue({
    branches: [{ name: 'main', hash: 'e'.repeat(64) }],
  })
  api.run.mockResolvedValue(result)
  const props = { env: 'sandbox', model: draft.model, draft, dirty: false }
  const view = render(
    <CandidateExecutionPanel
      {...props}
      compilation={{ ...compilation, revision_digest: 'old' }}
    />,
  )
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'Run candidate' })
      .disabled,
  ).toBe(true)
  view.rerender(
    <CandidateExecutionPanel {...props} compilation={compilation} />,
  )
  await userEvent.click(screen.getByRole('button', { name: 'Run candidate' }))
  await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
  expect(api.run).toHaveBeenCalledWith({
    data: expect.objectContaining({
      draft_id: draft.draft_id,
      revision: 3,
      revision_digest: draft.revision_digest,
      base_hash: 'e'.repeat(64),
      idempotency_key: expect.any(String),
    }),
  })
  expect(
    (await screen.findByText(/Native run actual-native-run/)).textContent,
  ).toContain('actual-native-plan')
  expect(screen.getByText(/snapshot 9223372036854775807/)).toBeTruthy()
  await act(() => finishHistory({ items: [] }))
  expect(screen.getByText(/Native run actual-native-run/)).toBeTruthy()
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'Run new candidate' })
      .disabled,
  ).toBe(false)
})
it('restores an existing run without launching another candidate', async () => {
  api.list.mockResolvedValue({ items: [result] })
  render(
    <CandidateExecutionPanel
      env="sandbox"
      model={draft.model}
      draft={draft}
      dirty={false}
      compilation={compilation}
    />,
  )
  await screen.findByText(/Native run actual-native-run/)
  expect(api.run).not.toHaveBeenCalled()
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'Run new candidate' })
      .disabled,
  ).toBe(false)
})

it('selects a real earlier native run and keeps its snapshot separate from the latest result', async () => {
  const earlier = {
    ...result,
    execution_id: 'earlier-execution',
    created_at: '2026-10-22T09:00:00Z',
    candidate_ref: 'sandbox-transform-earlier',
    report: { ...result.report, run_id: 'earlier-native-run' },
    preview: {
      ...result.preview,
      rows: [{ batch_id: 'EARLIER' }],
      snapshot_id: '9007199254740999',
    },
  }
  const later = { ...result, created_at: '2026-10-22T10:00:00Z' }
  api.list.mockResolvedValue({ items: [earlier, later] })
  render(
    <CandidateExecutionPanel
      env="sandbox"
      model={draft.model}
      draft={draft}
      dirty={false}
      compilation={compilation}
    />,
  )
  await screen.findByText(/Native run actual-native-run/)
  await userEvent.click(screen.getByRole('button', { name: 'Run history · 2' }))
  await userEvent.click(
    screen.getByRole('button', { name: /earlier-native-run/ }),
  )
  expect(
    (await screen.findByText(/Native run earlier-native-run/)).textContent,
  ).toContain('actual-native-plan')
  expect(screen.getByText('EARLIER')).toBeTruthy()
  expect(screen.getByText(/snapshot 9007199254740999/)).toBeTruthy()
  expect(api.run).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Diagnostics' }))
  expect(screen.getByText('Native run events and model results')).toBeTruthy()
})
