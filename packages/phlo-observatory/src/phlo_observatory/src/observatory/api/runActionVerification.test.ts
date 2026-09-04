/**
 * Tests the bounded verify-after-action semantics: the frozen
 * proven / pending-incomplete / failed states over durable canonical
 * evidence, retry-vs-cancel variants, exact action-result-to-run/report
 * resolution, and the bounded, cancellable, never-resubmitting poller.
 */
import { describe, expect, it, vi } from 'vitest'

import {
  classifyRunActionEvidence,
  findDurableReportIdentity,
  resolveVerificationTarget,
  startRunActionVerification,
} from './runActionVerification'
import type { RunActionResult } from './runActions'
import type { ObservatoryRun, ObservatoryRunReport } from './types'

function actionResult(
  overrides: Partial<RunActionResult> = {},
): RunActionResult {
  return {
    contract_version: 1,
    action_kind: 'run.retry',
    status: 'accepted',
    verification_handle: 'vh-abc123',
    target: { run_id: 'run-123' },
    resulting_run: { run_id: 'run-new-456' },
    canonical_report: null,
    canonical_report_path: null,
    provider: {},
    message: '',
    ...overrides,
  }
}

function report(overrides: Partial<ObservatoryRunReport> = {}) {
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
    ...overrides,
  } as ObservatoryRunReport
}

describe('resolveVerificationTarget', () => {
  it('resolves a reconciled retry to the exact canonical identity', () => {
    const target = resolveVerificationTarget(
      actionResult({
        status: 'reconciled',
        canonical_report: {
          project_id: 'finance',
          run_id: 'run-new-456',
          attempt: 2,
        },
      }),
      'finance',
    )
    expect(target).toEqual({
      kind: 'exact',
      identity: { project_id: 'finance', run_id: 'run-new-456', attempt: 2 },
    })
  })

  it('resolves an accepted retry with a distinct resulting run to discovery', () => {
    const target = resolveVerificationTarget(actionResult(), 'finance')
    expect(target).toEqual({
      kind: 'discover',
      runId: 'run-new-456',
      projectId: 'finance',
    })
  })

  it('resolves cancel to its target run, exact when reconciled', () => {
    const discovered = resolveVerificationTarget(
      actionResult({ action_kind: 'run.cancel', resulting_run: null }),
      'finance',
    )
    expect(discovered).toEqual({
      kind: 'discover',
      runId: 'run-123',
      projectId: 'finance',
    })

    const exact = resolveVerificationTarget(
      actionResult({
        action_kind: 'run.cancel',
        resulting_run: null,
        status: 'reconciled',
        canonical_report: {
          project_id: 'finance',
          run_id: 'run-123',
          attempt: 1,
        },
      }),
      'finance',
    )
    expect(exact).toEqual({
      kind: 'exact',
      identity: { project_id: 'finance', run_id: 'run-123', attempt: 1 },
    })
  })

  it('never maps a pending retry without a resulting run to anything verifiable', () => {
    const target = resolveVerificationTarget(
      actionResult({ status: 'pending', resulting_run: null }),
      'finance',
    )
    expect(target.kind).toBe('unverifiable')
  })
})

describe('classifyRunActionEvidence', () => {
  it('proves a retry only from complete evidence with terminal success', () => {
    const verification = classifyRunActionEvidence('run.retry', report())
    expect(verification.state).toBe('proven')
    expect(verification.identity).toEqual({
      project_id: 'finance',
      run_id: 'run-new-456',
      attempt: 2,
    })
  })

  it('records failure when complete evidence ends the retried run otherwise', () => {
    for (const status of ['failed', 'error', 'cancelled', 'skipped']) {
      const verification = classifyRunActionEvidence(
        'run.retry',
        report({
          terminal_outcome: { status, source: 'dagster', evidence_id: 'e1' },
        }),
      )
      expect(verification.state).toBe('failed')
      expect(verification.detail).toContain(status)
    }
  })

  it('proves a cancel only from complete evidence with terminal cancelled', () => {
    const verification = classifyRunActionEvidence(
      'run.cancel',
      report({
        run_id: 'run-123',
        attempt: 1,
        terminal_outcome: {
          status: 'cancelled',
          source: 'dagster',
          evidence_id: 'e1',
        },
        lifecycle: {
          run: {
            project_id: 'finance',
            run_id: 'run-123',
            attempt: 1,
            status: 'cancelled',
            evidence_completeness: 'complete',
          },
          events: [],
        },
      }),
    )
    expect(verification.state).toBe('proven')
  })

  it('records failure when a cancelled run instead finished another way', () => {
    const verification = classifyRunActionEvidence(
      'run.cancel',
      report({
        run_id: 'run-123',
        attempt: 1,
        lifecycle: {
          run: {
            project_id: 'finance',
            run_id: 'run-123',
            attempt: 1,
            status: 'success',
            evidence_completeness: 'complete',
          },
          events: [],
        },
      }),
    )
    expect(verification.state).toBe('failed')
  })

  it('stays pending-incomplete when evidence is incomplete, missing its terminal outcome, or gapped', () => {
    const incomplete = classifyRunActionEvidence(
      'run.retry',
      report({
        lifecycle: {
          run: {
            project_id: 'finance',
            run_id: 'run-new-456',
            attempt: 2,
            status: 'running',
            evidence_completeness: 'incomplete',
          },
          events: [],
        },
        terminal_outcome: null,
      }),
    )
    expect(incomplete.state).toBe('pending-incomplete')
    expect(incomplete.detail).toContain('incomplete')
    expect(incomplete.detail).toContain('terminal outcome')

    const gapped = classifyRunActionEvidence(
      'run.retry',
      report({
        gaps: [{ field: 'quality', status: 'missing', reason: 'no checks' }],
      }),
    )
    expect(gapped.state).toBe('pending-incomplete')
    expect(gapped.gaps).toEqual(['quality'])
  })
})

describe('findDurableReportIdentity', () => {
  const durableRun = (overrides: Partial<ObservatoryRun>): ObservatoryRun =>
    ({
      id: 'finance/run-new-456',
      name: 'run-new-456',
      status: 'running',
      assets: [],
      checks: [],
      logs: [],
      metadata: { source: 'durable_run_evidence' },
      ...overrides,
    }) as ObservatoryRun

  it('matches only durable rows that carry a report identity', () => {
    const target = {
      kind: 'discover',
      runId: 'run-new-456',
      projectId: 'finance',
    } as const
    const identity = {
      project_id: 'finance',
      run_id: 'run-new-456',
      attempt: 2,
    }

    expect(
      findDurableReportIdentity(
        [durableRun({ report_identity: identity })],
        target,
      ),
    ).toEqual(identity)
    // Legacy/provider rows never receive a report identity and are never promoted.
    expect(
      findDurableReportIdentity(
        [durableRun({ id: 'finance/run-new-456', report_identity: null })],
        target,
      ),
    ).toBeNull()
    // A durable row for another project never matches.
    expect(
      findDurableReportIdentity(
        [
          durableRun({
            report_identity: {
              project_id: 'other',
              run_id: 'run-new-456',
              attempt: 2,
            },
          }),
        ],
        target,
      ),
    ).toBeNull()
  })
})

describe('startRunActionVerification', () => {
  function lookups(
    overrides: Partial<{
      listRuns: () => Promise<Array<ObservatoryRun> | null>
      getReport: () => Promise<ObservatoryRunReport | null>
    }> = {},
  ) {
    return {
      listRuns:
        overrides.listRuns ??
        ((): Promise<Array<ObservatoryRun> | null> => Promise.resolve(null)),
      getReport:
        overrides.getReport ??
        ((): Promise<ObservatoryRunReport | null> => Promise.resolve(null)),
    }
  }

  it('classifies immediately from an exact identity and stops on a terminal state', async () => {
    const onState = vi.fn()
    const getReport = vi.fn(
      (): Promise<ObservatoryRunReport | null> => Promise.resolve(report()),
    )
    const stop = startRunActionVerification({
      actionKind: 'run.retry',
      target: {
        kind: 'exact',
        identity: { project_id: 'finance', run_id: 'run-new-456', attempt: 2 },
      },
      lookups: lookups({ getReport }),
      pollDelayMs: 0,
      onState,
    })
    await vi.waitFor(() => expect(onState).toHaveBeenCalledTimes(1))
    expect(onState.mock.calls[0][0].state).toBe('proven')
    expect(getReport).toHaveBeenCalledTimes(1)
    stop()
  })

  it('discovers the durable identity from the run read model before reading the report', async () => {
    const onState = vi.fn()
    const listRuns = vi.fn(() =>
      Promise.resolve([
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
            run_id: 'run-new-456',
            attempt: 2,
          },
        },
      ] as Array<ObservatoryRun>),
    )
    const getReport = vi.fn(
      (): Promise<ObservatoryRunReport | null> => Promise.resolve(report()),
    )
    const stop = startRunActionVerification({
      actionKind: 'run.retry',
      target: { kind: 'discover', runId: 'run-new-456', projectId: 'finance' },
      lookups: lookups({ listRuns, getReport }),
      pollDelayMs: 0,
      onState,
    })
    await vi.waitFor(() => expect(onState).toHaveBeenCalledTimes(1))
    expect(getReport).toHaveBeenCalledWith({
      project_id: 'finance',
      run_id: 'run-new-456',
      attempt: 2,
    })
    expect(onState.mock.calls[0][0].state).toBe('proven')
    stop()
  })

  it('emits an explicit pending state without polling for an unverifiable outcome', () => {
    const onState = vi.fn()
    const listRuns = vi.fn()
    const getReport = vi.fn()
    const stop = startRunActionVerification({
      actionKind: 'run.retry',
      target: { kind: 'unverifiable', reason: 'no resulting run' },
      lookups: lookups({
        listRuns: listRuns as never,
        getReport: getReport as never,
      }),
      onState,
    })
    expect(onState).toHaveBeenCalledTimes(1)
    const state = onState.mock.calls[0][0]
    expect(state.state).toBe('pending-incomplete')
    expect(state.unknownOutcome).toBe(true)
    expect(state.identity).toBeNull()
    expect(listRuns).not.toHaveBeenCalled()
    expect(getReport).not.toHaveBeenCalled()
    stop()
  })

  it('ends in an explicit pending state after its bounded window without proving anything', async () => {
    const onState = vi.fn()
    const getReport = vi.fn(
      (): Promise<ObservatoryRunReport | null> => Promise.resolve(null),
    )
    const stop = startRunActionVerification({
      actionKind: 'run.retry',
      target: {
        kind: 'exact',
        identity: { project_id: 'finance', run_id: 'run-new-456', attempt: 2 },
      },
      lookups: lookups({ getReport }),
      pollDelayMs: 0,
      maxPolls: 3,
      onState,
    })
    await vi.waitFor(() => expect(getReport).toHaveBeenCalledTimes(3))
    await vi.waitFor(() => expect(onState).toHaveBeenCalledTimes(1))
    const state = onState.mock.calls[0][0]
    expect(state.state).toBe('pending-incomplete')
    expect(state.detail).toContain('bounded window')
    stop()
  })

  it('keeps polling past not-found and incomplete evidence until evidence completes', async () => {
    const onState = vi.fn()
    const reports = [null, report({ terminal_outcome: null }), report()]
    const getReport = vi.fn((): Promise<ObservatoryRunReport | null> => {
      const next = reports.shift()
      return Promise.resolve(next ?? null)
    })
    const stop = startRunActionVerification({
      actionKind: 'run.retry',
      target: {
        kind: 'exact',
        identity: { project_id: 'finance', run_id: 'run-new-456', attempt: 2 },
      },
      lookups: lookups({ getReport }),
      pollDelayMs: 0,
      onState,
    })
    await vi.waitFor(() => expect(getReport).toHaveBeenCalledTimes(3))
    await vi.waitFor(() => expect(onState).toHaveBeenCalledTimes(2))
    // The intermediate incomplete report stays pending-incomplete with its
    // gaps named; only the complete report proves recovery.
    expect(onState.mock.calls[0][0].state).toBe('pending-incomplete')
    expect(onState.mock.calls[1][0].state).toBe('proven')
    stop()
  })

  it('stops without further lookups or states once cancelled', async () => {
    const onState = vi.fn()
    let resolveFirst: ((value: ObservatoryRunReport | null) => void) | undefined
    const firstReport = new Promise<ObservatoryRunReport | null>((resolve) => {
      resolveFirst = resolve
    })
    const getReport = vi
      .fn<() => Promise<ObservatoryRunReport | null>>()
      .mockImplementationOnce(() => firstReport)
      .mockResolvedValue(report())
    const stop = startRunActionVerification({
      actionKind: 'run.retry',
      target: {
        kind: 'exact',
        identity: { project_id: 'finance', run_id: 'run-new-456', attempt: 2 },
      },
      lookups: lookups({ getReport }),
      pollDelayMs: 0,
      onState,
    })
    stop()
    resolveFirst?.(null)
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(getReport).toHaveBeenCalledTimes(1)
    expect(onState).not.toHaveBeenCalled()
  })
})
