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

const resourceClients = vi.hoisted(() => ({
  getObservatoryPipelineRecords: vi.fn(),
  getObservatoryRunRecords: vi.fn(),
  getObservatoryRunReport: vi.fn(),
}))

vi.mock('@/observatory/api/resources', () => resourceClients)

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

/** A complete canonical run report for the retried run's new attempt. */
function completeReport() {
  return {
    schema_version: 1,
    project_id: 'finance',
    run_id: 'run-new-456',
    attempt: 2,
    lifecycle: {
      run: {
        project_id: 'finance',
        run_id: 'run-new-456',
        attempt: 2,
        status: 'success',
        evidence_completeness: 'complete',
      },
      events: [],
    },
    stages: [],
    inputs: [],
    staging: [],
    outputs: [],
    lineage: [],
    transformations: [],
    quality: [],
    iceberg_snapshots: [],
    catalog_changes: [],
    artifacts: [],
    terminal_outcome: {
      status: 'success',
      source: 'dagster',
      evidence_id: 'e1',
    },
    gaps: [],
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
    resourceClients.getObservatoryRunRecords.mockReset()
    resourceClients.getObservatoryRunReport.mockReset()
    resourceClients.getObservatoryRunRecords.mockResolvedValue({
      data: [],
      error: null,
    })
    resourceClients.getObservatoryRunReport.mockResolvedValue({
      data: null,
      error: 'No report was found for the requested project, run, and attempt.',
      errorCode: 'not_found',
    })
    resourceClients.getObservatoryPipelineRecords.mockResolvedValue({
      data: [],
      error: null,
    })
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

  it('proves a retried run only from complete durable evidence, never from acceptance', async () => {
    retryObservatoryRun.mockResolvedValue({ data: result(), error: null })
    resourceClients.getObservatoryRunRecords.mockResolvedValue({
      data: [
        {
          id: 'finance/run-new-456',
          name: 'run-new-456',
          status: 'running',
          assets: [],
          checks: [],
          logs: [],
          metadata: {},
          report_identity: {
            project_id: 'finance',
            run_id: 'finance/daily-orders-2',
            attempt: 2,
          },
        },
      ],
      error: null,
    })
    resourceClients.getObservatoryRunReport.mockResolvedValue({
      data: completeReport(),
      error: null,
    })
    render(
      <RunActionDialog
        action={action()}
        onClose={() => {}}
        pipeline={pipeline}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm retry' }))
    await waitFor(() => expect(retryObservatoryRun).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(
        screen.getByText(
          'Retry proven: durable evidence records the new run succeeded.',
        ),
      ).toBeTruthy(),
    )
    // The outcome card still records only acceptance; the success claim comes
    // solely from the verification pane's complete canonical evidence.
    expect(screen.getByText('Retry accepted.')).toBeTruthy()
    // The report link renders only from the exact durable identity.
    expect(screen.getAllByText('Open canonical run report').length).toBe(1)
    // Verification is read-only: the mutation is never resubmitted.
    expect(retryObservatoryRun).toHaveBeenCalledTimes(1)
  })

  it('keeps an unknown provider outcome explicitly pending without fabricating a report link', async () => {
    retryObservatoryRun.mockResolvedValue({
      data: result({ status: 'pending', resulting_run: null }),
      error: null,
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
      expect(screen.getByText('Retry cannot be verified yet.')).toBeTruthy(),
    )
    expect(screen.queryByText('Open canonical run report')).toBeNull()
    expect(resourceClients.getObservatoryRunRecords).not.toHaveBeenCalled()
    expect(resourceClients.getObservatoryRunReport).not.toHaveBeenCalled()
  })

  it('lets the operator stop bounded verification without claiming success', async () => {
    retryObservatoryRun.mockResolvedValue({ data: result(), error: null })
    // The new run exists durably but its report never becomes readable:
    // evidence stays missing for the whole bounded window.
    resourceClients.getObservatoryRunRecords.mockResolvedValue({
      data: [
        {
          id: 'finance/finance/daily-orders-2',
          name: 'finance/daily-orders-2',
          status: 'running',
          assets: [],
          checks: [],
          logs: [],
          metadata: {},
          report_identity: {
            project_id: 'finance',
            run_id: 'finance/daily-orders-2',
            attempt: 2,
          },
        },
      ],
      error: null,
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
      expect(resourceClients.getObservatoryRunReport).toHaveBeenCalled(),
    )
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Stop verifying' }),
      ).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Stop verifying' }))
    await waitFor(() =>
      expect(
        screen.getByText(/Verification stopped before complete evidence/),
      ).toBeTruthy(),
    )
    // No proven or failed headline may appear after a stopped window: the
    // stopped dialog claims nothing about recovery.
    expect(
      screen.queryByText(/durable evidence records the new run succeeded/),
    ).toBeNull()
    expect(screen.queryByText(/Retry proven/)).toBeNull()
  })

  it('does not start verification for a rejected action', async () => {
    retryObservatoryRun.mockResolvedValue({
      data: result({ status: 'rejected', message: 'Run is not retryable.' }),
      error: null,
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
      expect(screen.getByText('Retry rejected by the provider.')).toBeTruthy(),
    )
    expect(screen.queryByText('Verification')).toBeNull()
    expect(resourceClients.getObservatoryRunRecords).not.toHaveBeenCalled()
    expect(resourceClients.getObservatoryRunReport).not.toHaveBeenCalled()
  })
})
