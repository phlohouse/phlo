// @vitest-environment jsdom
/** Lifecycle controls retain errors and never turn source export into release execution. */
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ArchiveEvidenceAction, SourceHandoffPanel } from './retained-evidence'
import { DraftLifecycleActions } from './draft-lifecycle'
import { ActionAvailabilityProvider } from './action-availability'
import type { SourceHandoff } from '@/lib/data/api/candidate-evidence'

const changeMetric = vi.hoisted(() => vi.fn())
vi.mock('@/lib/data/api/metrics', () => ({
  changeMetricLifecycle: changeMetric,
  exportMetricDraft: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    search,
  }: {
    children: React.ReactNode
    search: { branch: string }
  }) => <a href={`/branches?branch=${search.branch}`}>{children}</a>,
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it('requires a reason, preserves rejected archive intent and confirms restoration without deleting evidence', async () => {
  const user = userEvent.setup()
  const change = vi
    .fn()
    .mockRejectedValueOnce(new Error('Active execution cannot be archived.'))
    .mockResolvedValueOnce(undefined)
  const { rerender } = render(
    <ArchiveEvidenceAction
      archived={false}
      operation="archive"
      onChange={change}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'Archive evidence' }))
  const dialog = within(screen.getByRole('dialog'))
  expect(
    dialog.getByRole<HTMLButtonElement>('button', {
      name: 'Confirm archive',
    }).disabled,
  ).toBe(true)
  await user.type(dialog.getByLabelText('Reason'), 'Superseded candidate')
  await user.click(dialog.getByRole('button', { name: 'Confirm archive' }))
  expect((await dialog.findByRole('alert')).textContent).toContain(
    'Active execution',
  )
  expect(dialog.getByLabelText<HTMLTextAreaElement>('Reason').value).toBe(
    'Superseded candidate',
  )
  await user.click(dialog.getByRole('button', { name: 'Cancel' }))
  rerender(
    <ArchiveEvidenceAction
      archived
      operation="archive"
      reason="Superseded candidate"
      onChange={change}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'Restore evidence' }))
  await user.click(screen.getByRole('button', { name: 'Confirm restore' }))
  expect(change).toHaveBeenLastCalledWith({
    archived: false,
    reason: 'Superseded candidate',
  })
})

it('sends the selected draft version and keeps a conflict visible instead of reporting an archive', async () => {
  changeMetric.mockRejectedValue(new Error('Version changed; reload.'))
  const changed = vi.fn()
  render(
    <DraftLifecycleActions
      kind="metric"
      env="sandbox"
      id="qc"
      version={7}
      status="draft"
      onChanged={changed}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: 'Archive draft' }))
  await userEvent.click(screen.getByRole('button', { name: 'Confirm archive' }))
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Version changed',
  )
  expect(changeMetric).toHaveBeenCalledWith({
    data: { env: 'sandbox', id: 'qc', action: 'archive', expected_version: 7 },
  })
  expect(changed).not.toHaveBeenCalled()
})

it('shows stale source evidence and source files while routing release through Branches, never POST URLs', async () => {
  const handoff: SourceHandoff = {
    env: 'sandbox',
    kind: 'transform_revision',
    source_review_required: true,
    automatic_promotion: false,
    files: [
      { path: 'models/qc.sql', content: 'select 13 as count', sha256: 'sha' },
    ],
    candidate_evidence: {
      stale: true,
      eligible_for_release: false,
      reason: 'Base moved',
      base_hash: 'old',
      current_base_hash: 'new',
      candidate_hash: 'candidate',
      observed_candidate_hash: 'candidate',
    },
    release: {
      source_ref: 'sandbox-candidate',
      target_ref: 'main',
      checks_url: '/api/v1/branches/checks',
      trial_merge_url: '/api/v1/trial',
      merge_url: '/api/v1/merge',
      requires_signature: true,
      blocked_reason: 'Fresh checks required',
    },
  }
  render(
    <SourceHandoffPanel
      operation="handoff"
      load={() => Promise.resolve(handoff)}
    />,
  )
  await userEvent.click(
    screen.getByRole('button', { name: 'Prepare source handoff' }),
  )
  expect(await screen.findByText(/Historical evidence/)).toBeTruthy()
  await userEvent.click(screen.getByText('models/qc.sql'))
  expect(
    screen.getByRole('region', { name: 'Source file models/qc.sql' })
      .textContent,
  ).toBe('select 13 as count')
  expect(
    screen
      .getByRole('link', { name: 'Review branch checks and signed release' })
      .getAttribute('href'),
  ).toContain('/branches?branch=sandbox-candidate')
  expect(document.querySelector('a[href^="/api/"]')).toBeNull()
  expect(screen.queryByRole('button', { name: /promote|merge/i })).toBeNull()
})

it('fails closed when the new lifecycle operation is absent from preflight', () => {
  render(
    <ActionAvailabilityProvider
      env="sandbox"
      initial={{ env: 'sandbox', actions: {} }}
    >
      <ArchiveEvidenceAction
        archived={false}
        operation="v1_csv_upload_archive"
        onChange={vi.fn()}
      />
    </ActionAvailabilityProvider>,
  )
  expect(
    screen.getByRole<HTMLButtonElement>('button', {
      name: 'Archive evidence',
    }).disabled,
  ).toBe(true)
  expect(screen.getByText(/availability could not be verified/)).toBeTruthy()
})
