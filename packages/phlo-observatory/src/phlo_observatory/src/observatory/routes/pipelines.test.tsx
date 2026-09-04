// @vitest-environment jsdom

/**
 * Tests the guarded retry/cancel UI: the label-to-control guard, the safe
 * outcome rendering for each status, and the Pipeline-local confirm
 * dialog's one-intent-one-idempotency-key submission contract.
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ObservatoryAction } from '@/observatory/api/types'
import type { RunActionResult } from '@/observatory/api/runActions'
import {
  RunActionDialog,
  describeRunActionOutcome,
  isRunActionControl,
} from '@/routes/pipelines'

const runActionClients = vi.hoisted(() => ({
  newRunActionIdempotencyKey: () => 'run-action-test-key',
  retryObservatoryRun: vi.fn(),
  cancelObservatoryRun: vi.fn(),
}))

vi.mock('@/observatory/api/runActions', () => runActionClients)

const retryObservatoryRun = runActionClients.retryObservatoryRun
const cancelObservatoryRun = runActionClients.cancelObservatoryRun

vi.mock('@/observatory/routes/liveResource', () => ({
  invalidateCachedResources: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: (props: {
    to: string
    params: Record<string, string>
    children: React.ReactNode
  }) => (
    <a data-testid="report-link" data-to={props.to}>
      {props.children}
    </a>
  ),
  createFileRoute: () => () => ({}) as unknown as never,
}))

function action(overrides: Partial<ObservatoryAction> = {}): ObservatoryAction {
  return {
    id: 'retry',
    label: 'Retry',
    kind: 'run.retry',
    enabled: true,
    requires_confirmation: true,
    reason: null,
    risk_level: 'high',
    required_capability: 'orchestrator_operations',
    required_permission: 'lakehouse:operate',
    equivalent_cli_command: null,
    expected_evidence: ['run.retry.verification_handle'],
    background_operation_id: 'finance/daily-orders',
    ...overrides,
  }
}

function result(overrides: Partial<RunActionResult> = {}): RunActionResult {
  return {
    contract_version: 1,
    action_kind: 'run.retry',
    status: 'accepted',
    verification_handle: 'vh-abc123',
    target: { run_id: 'finance/daily-orders' },
    resulting_run: { run_id: 'finance/daily-orders-2' },
    canonical_report: null,
    canonical_report_path: null,
    provider: {},
    message: '',
    ...overrides,
  }
}

describe('isRunActionControl', () => {
  it('accepts an enabled run action with an exact target run', () => {
    expect(isRunActionControl(action())).toBe(true)
  })

  it('hides the control when the run target is missing', () => {
    expect(isRunActionControl(action({ background_operation_id: null }))).toBe(
      false,
    )
  })

  it('hides the control when the contract disables the action', () => {
    expect(isRunActionControl(action({ enabled: false }))).toBe(false)
  })

  it('never turns non-run labels into controls', () => {
    expect(
      isRunActionControl(
        action({
          id: 'materialize',
          kind: 'asset.materialize',
          label: 'Materialize',
        }),
      ),
    ).toBe(false)
  })
})

describe('describeRunActionOutcome', () => {
  it('renders accepted, pending, reconciled, rejected, and skipped safely', () => {
    expect(describeRunActionOutcome(result()).headline).toBe('Retry accepted.')
    expect(describeRunActionOutcome(result({ status: 'pending' })).tone).toBe(
      'warning',
    )
    const reconciled = describeRunActionOutcome(
      result({
        status: 'reconciled',
        canonical_report: {
          project_id: 'finance',
          run_id: 'daily-orders-2',
          attempt: 2,
        },
      }),
    )
    expect(reconciled.detail).toContain('finance/daily-orders-2/2')
    expect(
      describeRunActionOutcome(
        result({ status: 'rejected', message: 'provider refused' }),
      ).tone,
    ).toBe('error')
    expect(describeRunActionOutcome(result({ status: 'skipped' })).tone).toBe(
      'warning',
    )
    expect(
      describeRunActionOutcome(
        result({ action_kind: 'run.cancel', status: 'accepted' }),
      ).headline,
    ).toBe('Cancel accepted.')
  })
})

describe('RunActionDialog', () => {
  beforeEach(() => {
    retryObservatoryRun.mockReset()
    cancelObservatoryRun.mockReset()
  })

  afterEach(cleanup)

  const pipeline = {
    dataset: { id: 'finance', name: 'Daily orders' },
    freshness_state: 'error',
    freshness_at: null,
    last_run: null,
    stages: [],
    actions: [],
  } as never

  it('explains capability, permission, risk, and evidence, then submits once', async () => {
    retryObservatoryRun.mockResolvedValue({
      data: result(),
      error: null,
    })
    render(
      <RunActionDialog
        action={action()}
        onClose={() => {}}
        pipeline={pipeline}
      />,
    )

    expect(screen.getByText('orchestrator_operations')).toBeTruthy()
    expect(screen.getByText('lakehouse:operate')).toBeTruthy()
    expect(screen.getByText('high risk')).toBeTruthy()
    expect(screen.getByText(/run\.retry\.verification_handle/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm retry' }))

    await waitFor(() => expect(retryObservatoryRun).toHaveBeenCalledTimes(1))
    expect(retryObservatoryRun).toHaveBeenCalledWith({
      data: {
        idempotencyKey: 'run-action-test-key',
        projectId: 'finance',
        runId: 'finance/daily-orders',
      },
    })
    await waitFor(() =>
      expect(screen.getByText('Retry accepted.')).toBeTruthy(),
    )
    expect(screen.getByText(/vh-abc123/)).toBeTruthy()
  })

  it('reuses one idempotency key when resubmitting after a transport failure', async () => {
    retryObservatoryRun.mockResolvedValue({
      data: null,
      error: 'phlo-api error: 401 Unauthorized',
    })
    render(
      <RunActionDialog
        action={action()}
        onClose={() => {}}
        pipeline={pipeline}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm retry' }))
    await waitFor(() =>
      expect(screen.getByText('Action could not be completed')).toBeTruthy(),
    )
    expect(screen.getByText(/401 Unauthorized/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Retry submission' }))
    await waitFor(() => expect(retryObservatoryRun).toHaveBeenCalledTimes(2))
    expect(retryObservatoryRun).toHaveBeenNthCalledWith(2, {
      data: {
        idempotencyKey: 'run-action-test-key',
        projectId: 'finance',
        runId: 'finance/daily-orders',
      },
    })
  })

  it('routes cancel intents to the cancel client with the target run', async () => {
    cancelObservatoryRun.mockResolvedValue({
      data: result({
        action_kind: 'run.cancel',
        status: 'reconciled',
        resulting_run: null,
      }),
      error: null,
    })
    render(
      <RunActionDialog
        action={action({
          id: 'cancel',
          kind: 'run.cancel',
          label: 'Cancel',
          risk_level: 'medium',
        })}
        onClose={() => {}}
        pipeline={pipeline}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm cancel' }))
    await waitFor(() =>
      expect(cancelObservatoryRun).toHaveBeenCalledWith({
        data: {
          idempotencyKey: 'run-action-test-key',
          projectId: 'finance',
          runId: 'finance/daily-orders',
        },
      }),
    )
    await waitFor(() =>
      expect(screen.getByText('Cancel reconciled.')).toBeTruthy(),
    )
  })
})
