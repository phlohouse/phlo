// @vitest-environment jsdom
/** Candidate execution submits a pinned revision and retains actual run/output identities. */
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { CandidateExecutionPanel } from './execution-panel'
import { RevisionLifecycle } from './revision-lifecycle'
import type {
  TransformCompilation,
  TransformDraft,
  TransformExecution,
} from '@/lib/data/api/transform-editor'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  run: vi.fn(),
  branches: vi.fn(),
  reconcile: vi.fn(),
  archive: vi.fn(),
  handoff: vi.fn(),
  reclaim: vi.fn(),
  reclaimDraft: vi.fn(),
}))
vi.mock('@/lib/data/api/transform-editor', () => ({
  getTransformExecutions: api.list,
  executeTransformDraft: api.run,
  reconcileTransformExecution: api.reconcile,
  archiveTransformExecution: api.archive,
  getTransformHandoff: api.handoff,
  reclaimTransformExecution: api.reclaim,
  reclaimTransformDraft: api.reclaimDraft,
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
  execution_id: '63947c63-2dfa-4da3-88be-6d2f791656ec',
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
it.each(['passed', 'unknown'] as const)(
  'reclaims archived %s execution payloads only when terminal and retains native reports',
  async (status) => {
    const archived = {
      ...result,
      status,
      archived_at: '2026-10-23T10:00:00Z',
      preview: status === 'passed' ? result.preview : null,
    }
    api.list.mockResolvedValue({ items: [{ ...archived, archived_at: null }] })
    api.archive.mockResolvedValue(archived)
    api.reclaim.mockResolvedValue({
      ...archived,
      reclaimed_at: '2026-10-23T12:00:00Z',
      reclaimed_bytes: 512,
      preview: null,
    })
    render(
      <CandidateExecutionPanel
        env="sandbox"
        model={draft.model}
        draft={draft}
        dirty={false}
      />,
    )
    const user = userEvent.setup()
    await user.click(
      await screen.findByRole('button', { name: 'Archive evidence' }),
    )
    await user.type(
      screen.getByLabelText('Reason'),
      'Retention review complete',
    )
    await user.click(screen.getByRole('button', { name: 'Confirm archive' }))
    const reclaim = await screen.findByRole<HTMLButtonElement>('button', {
      name: 'Reclaim execution payload',
    })
    if (status === 'unknown') {
      expect(reclaim.disabled).toBe(true)
      expect(
        screen.getByText(
          /Active or unresolved execution payloads cannot be reclaimed/,
        ),
      ).toBeTruthy()
      expect(api.reclaim).not.toHaveBeenCalled()
      return
    }
    await user.click(reclaim)
    await user.type(
      screen.getByLabelText('Confirmation identity'),
      result.execution_id,
    )
    await user.type(
      screen.getByLabelText('Reason'),
      'Retention review complete',
    )
    await user.click(
      screen.getByRole('button', { name: 'Permanently reclaim' }),
    )
    await screen.findByText(/512 local bytes reclaimed/)
    expect(api.reclaim).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        model: draft.model,
        draft_id: draft.draft_id,
        revision: 3,
        execution_id: result.execution_id,
        expected_revision_digest: draft.revision_digest,
        confirmed: true,
        reason: 'Retention review complete',
      },
    })
    expect(
      screen.getByRole<HTMLButtonElement>('button', {
        name: 'Restore evidence',
      }).disabled,
    ).toBe(true)
    expect(screen.getByText(/Native run actual-native-run/)).toBeTruthy()
    expect(screen.queryByText('B01')).toBeNull()
    expect(
      screen.getByRole('button', { name: 'Prepare source handoff' }),
    ).toBeTruthy()
  },
)
it('reclaims the pinned revision workspace and keeps its source handoff readable but restoration disabled', async () => {
  const archived = { ...draft, archived_at: '2026-10-23T10:00:00Z' }
  const reclaimed = {
    ...archived,
    reclaimed_at: '2026-10-23T12:00:00Z',
    reclaimed_bytes: 1024,
  }
  api.reclaimDraft.mockResolvedValue(reclaimed)
  const changed = vi.fn()
  const props = {
    env: 'sandbox',
    model: draft.model,
    disabled: false,
    onChanged: changed,
  }
  const view = render(<RevisionLifecycle {...props} draft={archived} />)
  const user = userEvent.setup()
  await user.click(
    screen.getByRole('button', { name: 'Reclaim revision payload' }),
  )
  await user.type(
    screen.getByLabelText('Confirmation identity'),
    `${draft.draft_id}:3`,
  )
  await user.type(screen.getByLabelText('Reason'), 'Retention review complete')
  await user.click(screen.getByRole('button', { name: 'Permanently reclaim' }))
  expect(changed).toHaveBeenCalledWith(reclaimed)
  expect(api.reclaimDraft).toHaveBeenCalledWith({
    data: {
      env: 'sandbox',
      model: draft.model,
      draft_id: draft.draft_id,
      revision: 3,
      expected_revision_digest: draft.revision_digest,
      confirmed: true,
      reason: 'Retention review complete',
    },
  })
  view.rerender(<RevisionLifecycle {...props} draft={reclaimed} />)
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'Restore evidence' })
      .disabled,
  ).toBe(true)
  expect(
    screen.getByRole('button', { name: 'Prepare source handoff' }),
  ).toBeTruthy()
  expect(screen.getByText(/1024 local bytes reclaimed/)).toBeTruthy()
})
it('clears prepared handoff eligibility and download after execution archive and restore', async () => {
  const user = userEvent.setup()
  api.list.mockResolvedValue({ items: [result] })
  api.handoff.mockResolvedValue({
    env: 'sandbox',
    kind: 'transform_revision',
    source_review_required: true,
    automatic_promotion: false,
    files: [],
    candidate_evidence: {
      stale: false,
      eligible_for_release: true,
      reason: null,
      base_hash: 'base',
      current_base_hash: 'base',
      candidate_hash: 'candidate',
      observed_candidate_hash: 'candidate',
    },
    release: {
      source_ref: null,
      target_ref: 'main',
      checks_url: null,
      trial_merge_url: null,
      merge_url: null,
      requires_signature: true,
      blocked_reason: null,
    },
  })
  api.archive
    .mockResolvedValueOnce({ ...result, archived_at: '2026-10-23T12:00:00Z' })
    .mockResolvedValueOnce({ ...result, archived_at: null })
  render(
    <CandidateExecutionPanel
      env="sandbox"
      model={draft.model}
      draft={draft}
      dirty={false}
    />,
  )
  await user.click(
    await screen.findByRole('button', { name: 'Prepare source handoff' }),
  )
  expect(
    await screen.findByRole('button', { name: 'Download review bundle' }),
  ).toBeTruthy()
  expect(screen.getAllByText(/Candidate evidence verified/)).toHaveLength(1)
  for (const action of ['archive', 'restore']) {
    await user.click(
      screen.getByRole('button', {
        name: action === 'archive' ? 'Archive evidence' : 'Restore evidence',
      }),
    )
    await user.type(
      screen.getByRole('textbox', { name: 'Reason' }),
      'Lifecycle transition',
    )
    await user.click(
      screen.getByRole('button', {
        name: action === 'archive' ? 'Confirm archive' : 'Confirm restore',
      }),
    )
    await screen.findByRole('button', {
      name: action === 'archive' ? 'Restore evidence' : 'Archive evidence',
    })
    expect(
      screen.queryByRole('button', { name: 'Download review bundle' }),
    ).toBeNull()
    expect(screen.queryByText(/Candidate evidence verified/)).toBeNull()
    if (action === 'archive') {
      await user.click(
        screen.getByRole('button', { name: 'Prepare source handoff' }),
      )
      await screen.findByRole('button', { name: 'Download review bundle' })
    }
  }
})
it('invalidates revision source handoff when archive state changes without changing revision identity', async () => {
  api.handoff.mockResolvedValue({
    env: 'sandbox',
    kind: 'transform_revision',
    source_review_required: true,
    automatic_promotion: false,
    files: [],
    candidate_evidence: null,
    release: {
      source_ref: null,
      target_ref: 'main',
      checks_url: null,
      trial_merge_url: null,
      merge_url: null,
      requires_signature: true,
      blocked_reason: null,
    },
  })
  const props = {
    env: 'sandbox',
    model: draft.model,
    draft,
    disabled: false,
    onChanged: vi.fn(),
  }
  const { rerender } = render(<RevisionLifecycle {...props} />)
  await userEvent.click(
    screen.getByRole('button', { name: 'Prepare source handoff' }),
  )
  await screen.findByRole('button', { name: 'Download review bundle' })
  rerender(
    <RevisionLifecycle
      {...props}
      draft={{ ...draft, archived_at: '2026-10-23T12:00:00Z' }}
    />,
  )
  expect(
    screen.queryByRole('button', { name: 'Download review bundle' }),
  ).toBeNull()
  expect(
    screen.getByRole('button', { name: 'Prepare source handoff' }),
  ).toBeTruthy()
})
it('reconciles unknown evidence without rerunning and keeps an unresolved verdict explicit', async () => {
  api.list.mockResolvedValue({
    items: [{ ...result, status: 'unknown', preview: null }],
  })
  api.reconcile.mockResolvedValue({
    ...result,
    status: 'unknown',
    preview: null,
    reconciliation: {
      resolution: 'unresolved',
      reason: 'Durable report not found.',
    },
  })
  render(
    <CandidateExecutionPanel
      env="sandbox"
      model={draft.model}
      draft={draft}
      dirty={false}
    />,
  )
  await userEvent.click(
    await screen.findByRole('button', { name: 'Reconcile evidence' }),
  )
  expect(await screen.findByText(/Reconciliation: unresolved/)).toBeTruthy()
  expect(screen.getByText(/Durable report not found/)).toBeTruthy()
  expect(api.run).not.toHaveBeenCalled()
})
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
