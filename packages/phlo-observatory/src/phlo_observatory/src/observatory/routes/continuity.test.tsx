// @vitest-environment jsdom

/**
 * Tests the continuity actions surface: capability gating from the inventory,
 * plan-first exact confirmation, apply-once under one idempotency key,
 * intent-closing unknown outcomes, and canonical evidence rendering.
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ContinuityApplyResult,
  ContinuityPlan,
  ContinuityVerificationEntry,
} from '@/observatory/api/continuity'

import { Continuity } from '@/routes/continuity'

const continuityApi = vi.hoisted(() => ({
  getContinuityOperations: vi.fn(),
  planContinuityOperation: vi.fn(),
  applyContinuityOperation: vi.fn(),
  getContinuityVerification: vi.fn(),
  newContinuityIdempotencyKey: () => 'continuity-test-key-1',
}))

vi.mock('@/observatory/api/continuity', async (importOriginal) => {
  const actual = await importOriginal<object>()
  return { ...actual, ...continuityApi }
})

vi.mock('@/observatory/routes/liveResource', () => ({
  loadCachedResource: (_key: string, load: () => Promise<unknown>) => load(),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: (props: { to: string; children: React.ReactNode }) => (
    <a data-testid="link" data-to={props.to}>
      {props.children}
    </a>
  ),
  createFileRoute: () => () => ({}) as unknown as never,
}))

const OPERATIONS_INVENTORY = {
  operations: [
    {
      operation: 'maintenance.plan',
      family: 'maintenance',
      surface: 'plan',
      requires_confirmation: false,
      description: 'Deterministic maintenance plan.',
    },
    {
      operation: 'backup.create',
      family: 'backup',
      surface: 'apply',
      requires_confirmation: false,
      description: 'Create one verified backup set.',
    },
    {
      operation: 'restore.plan',
      family: 'restore',
      surface: 'plan',
      requires_confirmation: false,
      description: 'Restore plan bound to set digest and target.',
    },
    {
      operation: 'upgrade.plan',
      family: 'upgrade',
      surface: 'plan',
      requires_confirmation: false,
      description: 'Upgrade plan for the supported pair.',
    },
  ],
  unsupported: [
    {
      operation: 'orphan_delete',
      family: 'maintenance',
      reason: 'no bounded deletion set exists',
    },
  ],
}

const RESTORE_PLAN: ContinuityPlan = {
  kind: 'restore',
  planToken: 'plan-token-abc123',
  operationId: 'restore.apply:/tmp/target:deadbeef',
  plan: {
    schema_version: '1',
    plan_token: 'plan-token-abc123',
    backup_set_dir: '/tmp/set',
    backup_set_id: 'set-1',
    set_digest: 'digest-1',
    target: { target_id: '/tmp/target', location: '/tmp/target' },
    provider_order: ['nessie', 'minio'],
    created_at: '2026-09-03T00:00:00Z',
    expires_at: '2026-09-03T01:00:00Z',
  },
}

function succeededVerification(
  overrides: Partial<ContinuityVerificationEntry> = {},
): ContinuityVerificationEntry {
  return {
    operation_id: RESTORE_PLAN.operationId,
    subject: 'operator',
    action: 'restore.apply',
    target: '/tmp/target',
    plan_token: RESTORE_PLAN.planToken,
    state: 'succeeded',
    claim_expiry: '',
    result: { accepted: true },
    observation_time: '2026-09-03T00:05:00Z',
    replay_blocked: false,
    ...overrides,
  }
}

async function openRestoreAction() {
  continuityApi.getContinuityOperations.mockResolvedValue({
    data: OPERATIONS_INVENTORY,
    error: null,
  })
  render(<Continuity />)
  await screen.findByText('Supported operations')
  fireEvent.click(
    screen.getByRole('button', { name: 'Restore to explicit target' }),
  )
  await screen.findByText('Create dry-run plan')
}

async function planRestore() {
  continuityApi.planContinuityOperation.mockResolvedValue({
    data: RESTORE_PLAN,
    error: null,
  })
  fireEvent.change(screen.getByLabelText('Backup set directory'), {
    target: { value: '/tmp/set' },
  })
  fireEvent.change(screen.getByLabelText('Explicit restore target'), {
    target: { value: '/tmp/target' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Create dry-run plan' }))
  await screen.findByLabelText('Confirmation: retype the exact plan token')
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Continuity capability gating', () => {
  it('renders only inventory-listed operations and never an unsupported control', async () => {
    continuityApi.getContinuityOperations.mockResolvedValue({
      data: OPERATIONS_INVENTORY,
      error: null,
    })
    render(<Continuity />)
    await screen.findByText('Supported operations')

    for (const label of [
      'Backup create',
      'Restore to explicit target',
      'Version upgrade',
      'Maintenance',
    ]) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
    // orphan_delete appears only as explicitly unsupported text, never as a
    // button or actionable control.
    expect(screen.getByText(/orphan_delete/)).toBeTruthy()
    const orphanControls = screen.queryAllByRole('button', {
      name: /orphan/i,
    })
    expect(orphanControls).toEqual([])
  })

  it('hides a family control the backend inventory does not list', async () => {
    continuityApi.getContinuityOperations.mockResolvedValue({
      data: {
        operations: OPERATIONS_INVENTORY.operations.filter(
          (op) => op.family !== 'upgrade',
        ),
        unsupported: [],
      },
      error: null,
    })
    render(<Continuity />)
    await screen.findByText('Supported operations')
    expect(screen.queryByRole('button', { name: 'Version upgrade' })).toBeNull()
    expect(
      screen.getByRole('button', { name: 'Restore to explicit target' }),
    ).toBeTruthy()
  })

  it('renders no controls when the continuity API is unreachable', async () => {
    continuityApi.getContinuityOperations.mockResolvedValue({
      data: null,
      error: 'phlo-api error: 404',
    })
    render(<Continuity />)
    await screen.findByText('Continuity is not available in this stack')
    expect(screen.queryByText('Create dry-run plan')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Backup create' })).toBeNull()
  })
})

describe('Continuity plan-first confirmation and apply-once', () => {
  beforeEach(() => {
    continuityApi.getContinuityVerification.mockResolvedValue({
      data: null,
      error: null,
    })
  })

  it('enables apply only when the typed confirmation equals the plan token', async () => {
    await openRestoreAction()
    await planRestore()

    const applyButton = screen.getByRole('button', {
      name: 'Apply restore to explicit target',
    })
    const isDisabled = () => applyButton.hasAttribute('disabled')
    expect(isDisabled()).toBe(true)

    fireEvent.change(
      screen.getByLabelText('Confirmation: retype the exact plan token'),
      {
        target: { value: 'not-the-token' },
      },
    )
    expect(isDisabled()).toBe(true)

    fireEvent.change(
      screen.getByLabelText('Confirmation: retype the exact plan token'),
      {
        target: { value: RESTORE_PLAN.planToken },
      },
    )
    expect(isDisabled()).toBe(false)
  })

  it('applies once with the exact plan token and one idempotency key', async () => {
    continuityApi.applyContinuityOperation.mockResolvedValue({
      data: {
        operation: 'restore.apply',
        operation_id: RESTORE_PLAN.operationId,
        state: 'succeeded',
        accepted: true,
      } satisfies ContinuityApplyResult,
      error: null,
    })
    await openRestoreAction()
    await planRestore()

    fireEvent.change(
      screen.getByLabelText('Confirmation: retype the exact plan token'),
      {
        target: { value: RESTORE_PLAN.planToken },
      },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply restore to explicit target' }),
    )

    await waitFor(() => {
      expect(continuityApi.applyContinuityOperation).toHaveBeenCalledTimes(1)
    })
    const call = continuityApi.applyContinuityOperation.mock.calls[0][0].data
    expect(call.operation).toBe('restore.apply')
    expect(call.confirmationToken).toBe(RESTORE_PLAN.planToken)
    expect(call.idempotencyKey).toBe('continuity-test-key-1')
    expect(call.plan.plan_token).toBe(RESTORE_PLAN.planToken)
    expect(call.plan.target.target_id).toBe('/tmp/target')

    // The intent is closed: the button retires and the key is not regenerated.
    await screen.findByText('Applied')
    const appliedButton = screen.getByRole('button', {
      name: 'Applied',
    })
    expect(appliedButton.hasAttribute('disabled')).toBe(true)
    expect(screen.getByText('continuity-test-key-1')).toBeTruthy()
  })

  it('reuses the same idempotency key and plan token on a transport error', async () => {
    continuityApi.applyContinuityOperation
      .mockResolvedValueOnce({ data: null, error: 'network unreachable' })
      .mockResolvedValueOnce({
        data: {
          operation: 'restore.apply',
          operation_id: RESTORE_PLAN.operationId,
          state: 'succeeded',
          accepted: true,
        },
        error: null,
      })
    await openRestoreAction()
    await planRestore()

    fireEvent.change(
      screen.getByLabelText('Confirmation: retype the exact plan token'),
      {
        target: { value: RESTORE_PLAN.planToken },
      },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply restore to explicit target' }),
    )
    await screen.findByText('Apply could not be completed')

    // Still open: the same intent may resubmit under the same key.
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply restore to explicit target' }),
    )
    await waitFor(() => {
      expect(continuityApi.applyContinuityOperation).toHaveBeenCalledTimes(2)
    })
    for (const call of continuityApi.applyContinuityOperation.mock.calls) {
      expect(call[0].data.idempotencyKey).toBe('continuity-test-key-1')
      expect(call[0].data.confirmationToken).toBe(RESTORE_PLAN.planToken)
    }
  })

  it('closes the intent on an unknown outcome and never offers a new key', async () => {
    continuityApi.applyContinuityOperation.mockResolvedValue({
      data: null,
      error:
        'phlo-api error: 502 {"detail":{"error":"apply_outcome_unknown","identifiers":["restore.apply:/tmp/target:deadbeef"]}}',
    })
    await openRestoreAction()
    await planRestore()

    fireEvent.change(
      screen.getByLabelText('Confirmation: retype the exact plan token'),
      {
        target: { value: RESTORE_PLAN.planToken },
      },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply restore to explicit target' }),
    )

    await screen.findByText(/no new idempotency key may replay/i)
    // The apply intent is closed even though the backend answered with an
    // error: resubmission is gone, and the confirmation input is gone too.
    expect(
      screen.queryByRole('button', {
        name: 'Apply restore to explicit target',
      }),
    ).toBeNull()
    expect(screen.getByText('Applied')).toBeTruthy()
    expect(continuityApi.applyContinuityOperation).toHaveBeenCalledTimes(1)
  })
})

describe('Continuity canonical verification rendering', () => {
  it('proves the apply only from the durable journal evidence', async () => {
    continuityApi.applyContinuityOperation.mockResolvedValue({
      data: {
        operation: 'restore.apply',
        operation_id: RESTORE_PLAN.operationId,
        state: 'succeeded',
        accepted: true,
      },
      error: null,
    })
    continuityApi.getContinuityVerification.mockResolvedValue({
      data: succeededVerification(),
      error: null,
    })
    await openRestoreAction()
    await planRestore()

    fireEvent.change(
      screen.getByLabelText('Confirmation: retype the exact plan token'),
      {
        target: { value: RESTORE_PLAN.planToken },
      },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply restore to explicit target' }),
    )

    await screen.findByText('Continuity action proven by durable evidence.')
    expect(continuityApi.getContinuityVerification).toHaveBeenCalledWith({
      data: { operationId: RESTORE_PLAN.operationId },
    })
  })

  it('never claims success from incomplete or unknown evidence', async () => {
    continuityApi.applyContinuityOperation.mockResolvedValue({
      data: {
        operation: 'restore.apply',
        operation_id: RESTORE_PLAN.operationId,
        state: 'submitted',
        accepted: true,
      },
      error: null,
    })
    continuityApi.getContinuityVerification.mockResolvedValue({
      data: succeededVerification({
        state: 'unknown',
        replay_blocked: true,
        result: null,
      }),
      error: null,
    })
    await openRestoreAction()
    await planRestore()

    fireEvent.change(
      screen.getByLabelText('Confirmation: retype the exact plan token'),
      {
        target: { value: RESTORE_PLAN.planToken },
      },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply restore to explicit target' }),
    )

    await screen.findByText('Outcome unknown; replay is blocked.')
    expect(screen.queryByText(/proven/i)).toBeNull()
    expect(screen.getByText(/Replay blocked/)).toBeTruthy()
  })

  it('renders a terminal failed journal state as failed', async () => {
    continuityApi.applyContinuityOperation.mockResolvedValue({
      data: {
        operation: 'restore.apply',
        operation_id: RESTORE_PLAN.operationId,
        state: 'failed',
        accepted: false,
      },
      error: null,
    })
    continuityApi.getContinuityVerification.mockResolvedValue({
      data: succeededVerification({
        state: 'failed',
        result: { accepted: false, failure: { reason: 'provider refused' } },
      }),
      error: null,
    })
    await openRestoreAction()
    await planRestore()

    fireEvent.change(
      screen.getByLabelText('Confirmation: retype the exact plan token'),
      {
        target: { value: RESTORE_PLAN.planToken },
      },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply restore to explicit target' }),
    )

    await screen.findByText('Continuity action failed.')
  })
})
