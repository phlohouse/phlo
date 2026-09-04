/**
 * Bounded verify-after-action for guarded continuity actions.
 *
 * Uses the proven, pending-incomplete, and failed verification states over the
 * canonical verification lookup: only the durable operation journal entry,
 * bound to complete canonical evidence (a terminal
 * succeeded state AND the core service's acceptance evidence in the recorded
 * result), may turn an applied continuity action into a proven claim. Missing,
 * claimed, submitted, or incomplete evidence stays pending-incomplete — it
 * never renders as success.
 *
 * Verification polls only the canonical GET lookup. It never resubmits the
 * mutation, is bounded to a fixed poll window, and is cancellable. An unknown
 * post-submission outcome (`replay_blocked`) is surfaced as such: no new
 * idempotency key may replay the operation.
 */
import type { ContinuityVerificationEntry } from './continuity'

/** The three verification states. */
export type ContinuityVerificationState =
  | 'proven'
  | 'pending-incomplete'
  | 'failed'

export interface ContinuityActionVerification {
  state: ContinuityVerificationState
  headline: string
  detail: string
  /** The canonical durable handle this verification was resolved from. */
  operationId: string | null
  /** Named evidence gaps when the journal entry exists but is incomplete. */
  gaps: Array<string>
  /** True when the provider never named a distinct outcome to verify. */
  unknownOutcome: boolean
  /** True when the journal blocks any new-key replay for this operation. */
  replayBlocked: boolean
  /** Durable rollback / forward-repair evidence, when the backend recorded it. */
  recovery: {
    rollbackAction: string | null
    forwardRepair: boolean
    reconciliationOk: boolean | null
  } | null
}

function resultField(
  entry: ContinuityVerificationEntry,
  field: string,
): unknown {
  const result = entry.result
  return result && typeof result === 'object' ? result[field] : undefined
}

function recoveryEvidence(
  entry: ContinuityVerificationEntry,
): ContinuityActionVerification['recovery'] {
  if (entry.state !== 'succeeded' && entry.state !== 'failed') return null
  const reconciliation = resultField(entry, 'reconciliation')
  const forwardRepair = resultField(entry, 'forward_repair')
  return {
    rollbackAction:
      typeof resultField(entry, 'rollback_action') === 'string'
        ? (resultField(entry, 'rollback_action') as string)
        : null,
    forwardRepair:
      !!forwardRepair &&
      typeof forwardRepair === 'object' &&
      Object.keys(forwardRepair as Record<string, unknown>).length > 0,
    reconciliationOk:
      reconciliation && typeof reconciliation === 'object'
        ? (reconciliation as { ok?: boolean }).ok === true
        : null,
  }
}

/**
 * Classify one canonical journal entry against the continuity action's
 * expected semantics. Proven requires a terminal `succeeded` journal state
 * AND acceptance evidence inside the recorded result (`accepted === true` for
 * backup/restore/upgrade, `status === 'completed'` for maintenance). Anything
 * else — a missing entry, a claimed/submitted in-flight state, a succeeded
 * state without recorded acceptance, or any unknown outcome — stays
 * pending-incomplete with the gap named explicitly. A terminal `failed` state
 * is failed.
 */
export function classifyContinuityEvidence(
  entry: ContinuityVerificationEntry | null,
): ContinuityActionVerification {
  if (!entry) {
    return {
      state: 'pending-incomplete',
      headline: 'Continuity action not proven yet.',
      detail:
        'No durable journal entry is readable for this operation handle yet. Verification stays pending until the canonical evidence exists; no success is claimed.',
      operationId: null,
      gaps: ['no durable journal entry'],
      unknownOutcome: false,
      replayBlocked: false,
      recovery: null,
    }
  }

  const operationId = entry.operation_id
  const recovery = recoveryEvidence(entry)

  if (entry.state === 'unknown' || entry.replay_blocked) {
    return {
      state: 'pending-incomplete',
      headline: 'Outcome unknown; replay is blocked.',
      detail: `The durable journal for ${operationId} recorded a submission whose outcome was never observed. This verification can never become a success claim, and no new idempotency key may replay the operation. Resolve through the runbook before re-attempting.`,
      operationId,
      gaps: ['post-submission outcome not observed'],
      unknownOutcome: true,
      replayBlocked: true,
      recovery,
    }
  }

  if (entry.state === 'succeeded') {
    const accepted = resultField(entry, 'accepted')
    const status = resultField(entry, 'status')
    const acceptedEvidence = accepted === true || status === 'completed'
    if (!acceptedEvidence) {
      return {
        state: 'pending-incomplete',
        headline: 'Continuity action not proven yet.',
        detail: `The durable journal for ${operationId} records state "succeeded" without recorded acceptance evidence in the result. Verification stays pending until complete canonical evidence exists; no success is claimed.`,
        operationId,
        gaps: ['no acceptance evidence in recorded result'],
        unknownOutcome: false,
        replayBlocked: false,
        recovery,
      }
    }
    const evidence: Array<string> = []
    if (accepted === true) evidence.push('acceptance recorded')
    if (status === 'completed') evidence.push('maintenance completed')
    if (recovery?.reconciliationOk === true) {
      evidence.push('post-action reconciliation ok')
    }
    if (recovery?.reconciliationOk === false) {
      evidence.push('post-action reconciliation reported failures')
    }
    if (recovery?.rollbackAction) {
      evidence.push(`rollback action: ${recovery.rollbackAction}`)
    }
    if (recovery?.forwardRepair) {
      evidence.push('bounded forward-repair evidence recorded')
    }
    return {
      state: 'proven',
      headline: 'Continuity action proven by durable evidence.',
      detail: `Complete canonical evidence for ${operationId} records the terminal "succeeded" state with acceptance evidence (${evidence.join('; ') || 'recorded result'}). The action is proven, not just accepted.`,
      operationId,
      gaps: [],
      unknownOutcome: false,
      replayBlocked: false,
      recovery,
    }
  }

  if (entry.state === 'failed') {
    const failure = resultField(entry, 'failure')
    const reason =
      failure && typeof failure === 'object' && 'reason' in failure
        ? String((failure as { reason?: unknown }).reason)
        : 'no failure reason recorded'
    return {
      state: 'failed',
      headline: 'Continuity action failed.',
      detail: `The durable journal for ${operationId} records the terminal "failed" state (${reason}). The failure is recorded; no success is claimed.`,
      operationId,
      gaps: [],
      unknownOutcome: false,
      replayBlocked: entry.replay_blocked,
      recovery,
    }
  }

  // claimed / submitted: the durable claim exists but no terminal outcome yet.
  return {
    state: 'pending-incomplete',
    headline: 'Continuity action not proven yet.',
    detail: `The durable journal for ${operationId} holds the operation in the "${entry.state}" state. Verification stays pending until a terminal state with acceptance evidence exists; no success is claimed.`,
    operationId,
    gaps: [`journal state is "${entry.state}"`],
    unknownOutcome: false,
    replayBlocked: false,
    recovery: null,
  }
}

export const CONTINUITY_VERIFICATION_POLL_DELAY_MS = 5_000
export const CONTINUITY_VERIFICATION_MAX_POLLS = 12

/**
 * Run bounded verify-after-action against the canonical verification lookup.
 *
 * The poller reads the durable journal entry and classifies it. Any terminal
 * classification (proven/failed) stops the poller; otherwise it polls up to
 * `maxPolls` times and ends in an explicit pending-incomplete state.
 * `onState` receives every state change. Returns a cancel function; after
 * cancel, no further lookups happen and no further states are emitted.
 */
export function startContinuityVerification(options: {
  operationId: string
  lookup: (operationId: string) => Promise<ContinuityVerificationEntry | null>
  pollDelayMs?: number
  maxPolls?: number
  delay?: (ms: number) => Promise<void>
  onState: (verification: ContinuityActionVerification) => void
  /** Called once when the poller stops on its own (terminal or bounded window). */
  onDone?: () => void
}): () => void {
  const {
    operationId,
    lookup,
    pollDelayMs = CONTINUITY_VERIFICATION_POLL_DELAY_MS,
    maxPolls = CONTINUITY_VERIFICATION_MAX_POLLS,
    delay = (ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
    onState,
    onDone,
  } = options

  let cancelled = false

  void (async () => {
    for (let poll = 1; poll <= maxPolls; poll += 1) {
      const entry = await lookup(operationId)
      if (cancelled) return
      if (entry) {
        const verification = classifyContinuityEvidence(entry)
        onState(verification)
        if (verification.state !== 'pending-incomplete') {
          onDone?.()
          return
        }
        // An unknown outcome is a bounded terminal state for the poller: the
        // journal will not resolve it by itself, so stop and report it.
        if (verification.unknownOutcome) {
          onDone?.()
          return
        }
      }
      if (poll < maxPolls) {
        await delay(pollDelayMs)
        if (cancelled) return
      }
    }
    onState({
      state: 'pending-incomplete',
      headline: 'Continuity action not proven yet.',
      detail: `Verification stopped after its bounded window: durable canonical evidence for ${operationId} is still absent or incomplete. The apply outcome above remains the record of provider acceptance; no success is claimed.`,
      operationId,
      gaps: ['verification window elapsed without terminal evidence'],
      unknownOutcome: false,
      replayBlocked: false,
      recovery: null,
    })
    onDone?.()
  })()

  return () => {
    cancelled = true
  }
}
