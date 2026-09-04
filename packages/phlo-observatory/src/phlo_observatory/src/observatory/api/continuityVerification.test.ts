/**
 * Tests bounded continuity verify-after-action: the proven,
 * pending-incomplete, and failed states bound to canonical journal evidence,
 * and the bounded, cancellable, pure-GET poller.
 */
import { describe, expect, it, vi } from 'vitest'

import {
  CONTINUITY_VERIFICATION_MAX_POLLS,
  classifyContinuityEvidence,
  startContinuityVerification,
} from './continuityVerification'
import type { ContinuityVerificationEntry } from './continuity'

function entry(
  overrides: Partial<ContinuityVerificationEntry> = {},
): ContinuityVerificationEntry {
  return {
    operation_id: 'restore.apply:/tmp/target:abc123',
    subject: 'operator',
    action: 'restore.apply',
    target: '/tmp/target',
    plan_token: 'token-1',
    state: 'submitted',
    claim_expiry: '',
    result: null,
    observation_time: '2026-09-03T00:00:00Z',
    replay_blocked: false,
    ...overrides,
  }
}

const immediateDelay = () => Promise.resolve()

describe('classifyContinuityEvidence', () => {
  it('proves only a succeeded state with recorded acceptance evidence', () => {
    const proven = classifyContinuityEvidence(
      entry({ state: 'succeeded', result: { accepted: true } }),
    )
    expect(proven.state).toBe('proven')
    expect(proven.unknownOutcome).toBe(false)
    expect(proven.gaps).toEqual([])
    expect(proven.operationId).toBe('restore.apply:/tmp/target:abc123')
  })

  it('proves maintenance completion evidence', () => {
    const proven = classifyContinuityEvidence(
      entry({
        state: 'succeeded',
        result: { status: 'completed', operation: 'compact' },
      }),
    )
    expect(proven.state).toBe('proven')
  })

  it('stays pending-incomplete for a succeeded state without acceptance evidence', () => {
    const pending = classifyContinuityEvidence(
      entry({ state: 'succeeded', result: {} }),
    )
    expect(pending.state).toBe('pending-incomplete')
    expect(pending.gaps).toContain('no acceptance evidence in recorded result')
  })

  it('stays pending-incomplete for claimed and submitted states', () => {
    for (const state of ['claimed', 'submitted'] as const) {
      const pending = classifyContinuityEvidence(entry({ state }))
      expect(pending.state).toBe('pending-incomplete')
      expect(pending.unknownOutcome).toBe(false)
    }
  })

  it('classifies a terminal failed state as failed with the recorded reason', () => {
    const failed = classifyContinuityEvidence(
      entry({
        state: 'failed',
        result: { failure: { reason: 'digest mismatch' } },
      }),
    )
    expect(failed.state).toBe('failed')
    expect(failed.detail).toContain('digest mismatch')
  })

  it('reports an unknown outcome as pending-incomplete with replay blocked', () => {
    const unknown = classifyContinuityEvidence(
      entry({ state: 'unknown', replay_blocked: true }),
    )
    expect(unknown.state).toBe('pending-incomplete')
    expect(unknown.unknownOutcome).toBe(true)
    expect(unknown.replayBlocked).toBe(true)
    expect(unknown.detail).toContain('no new idempotency key may replay')
  })

  it('surfaces rollback and forward-repair evidence on the proven claim', () => {
    const proven = classifyContinuityEvidence(
      entry({
        state: 'succeeded',
        result: {
          accepted: true,
          rollback_action: 'restore verified backup set',
          forward_repair: { steps: 2 },
          reconciliation: { ok: true, checks: {}, reasons: [] },
        },
      }),
    )
    expect(proven.state).toBe('proven')
    expect(proven.recovery).toEqual({
      rollbackAction: 'restore verified backup set',
      forwardRepair: true,
      reconciliationOk: true,
    })
    expect(proven.detail).toContain('rollback action')
    expect(proven.detail).toContain('forward-repair')
  })

  it('stays pending-incomplete when no journal entry is readable', () => {
    const missing = classifyContinuityEvidence(null)
    expect(missing.state).toBe('pending-incomplete')
    expect(missing.operationId).toBeNull()
  })
})

describe('startContinuityVerification', () => {
  it('stops at the first terminal classification and never polls again', async () => {
    const lookup = vi.fn(() =>
      Promise.resolve(
        entry({ state: 'succeeded', result: { accepted: true } }),
      ),
    )
    const onState = vi.fn()
    const onDone = vi.fn()
    const cancel = startContinuityVerification({
      operationId: 'op-1',
      lookup,
      delay: immediateDelay,
      onState,
      onDone,
    })
    await Promise.resolve()
    cancel()
    expect(lookup).toHaveBeenCalledTimes(1)
    expect(onState).toHaveBeenCalledTimes(1)
    expect(onState.mock.calls[0][0].state).toBe('proven')
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('stops on an unknown outcome without waiting out the window', async () => {
    const lookup = vi.fn(() =>
      Promise.resolve(entry({ state: 'unknown', replay_blocked: true })),
    )
    const onState = vi.fn()
    const onDone = vi.fn()
    const cancel = startContinuityVerification({
      operationId: 'op-unknown',
      lookup,
      delay: immediateDelay,
      onState,
      onDone,
    })
    await Promise.resolve()
    cancel()
    expect(lookup).toHaveBeenCalledTimes(1)
    expect(onState.mock.calls[0][0].replayBlocked).toBe(true)
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('ends in an explicit pending-incomplete state after the bounded window', async () => {
    const lookup = vi.fn<() => Promise<ContinuityVerificationEntry | null>>(
      () => Promise.resolve(null),
    )
    const onState = vi.fn()
    const onDone = vi.fn()
    const cancel = startContinuityVerification({
      operationId: 'op-pending',
      lookup,
      maxPolls: 3,
      delay: immediateDelay,
      onState,
      onDone,
    })
    for (let tick = 0; tick < 20; tick += 1) {
      await Promise.resolve()
    }
    cancel()
    expect(lookup).toHaveBeenCalledTimes(3)
    expect(CONTINUITY_VERIFICATION_MAX_POLLS).toBeGreaterThan(0)
    const finalState = onState.mock.calls.at(-1)?.[0]
    expect(finalState.state).toBe('pending-incomplete')
    expect(finalState.detail).toContain('bounded window')
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('emits nothing after cancel', async () => {
    let resolveLookup: (
      value: ContinuityVerificationEntry | null,
    ) => void = () => {}
    const lookup = vi.fn(
      () =>
        new Promise<ContinuityVerificationEntry | null>((resolve) => {
          resolveLookup = resolve
        }),
    )
    const onState = vi.fn()
    const cancel = startContinuityVerification({
      operationId: 'op-cancel',
      lookup,
      onState,
    })
    cancel()
    resolveLookup(entry({ state: 'succeeded', result: { accepted: true } }))
    await Promise.resolve()
    expect(onState).not.toHaveBeenCalled()
  })
})
