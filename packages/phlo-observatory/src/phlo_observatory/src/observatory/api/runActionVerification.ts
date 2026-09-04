/**
 * Bounded verify-after-action for guarded run actions.
 *
 * Frozen verification semantics over durable canonical evidence: only a
 * complete canonical run report (complete run header, authoritative terminal
 * outcome, no report gaps) may turn an accepted retry
 * into a recovery claim or a cancel into a terminal-state claim. Missing,
 * legacy, or incomplete evidence stays pending-incomplete — it never renders
 * as success and never fabricates a report link.
 *
 * Verification polls only durable run/report reads (GET). It never resubmits
 * a mutation, is bounded to a fixed poll window, and is cancellable.
 */
import type {
  ObservatoryRun,
  ObservatoryRunReport,
  ObservatoryRunReportIdentity,
} from './types'
import type { RunActionKind, RunActionResult } from './runActions'

/** The three verification states a run action can end in. */
export type RunActionVerificationState =
  | 'proven'
  | 'pending-incomplete'
  | 'failed'

export interface RunActionVerification {
  state: RunActionVerificationState
  headline: string
  detail: string
  /**
   * The exact durable report identity backing this verification, when one is
   * known. A report link may be rendered only from this identity — it is
   * populated exclusively from durable evidence or the durable run read model,
   * never from provider payloads or display
   * names.
   */
  identity: ObservatoryRunReportIdentity | null
  /** Canonical report gap field names, when the report exists but is incomplete. */
  gaps: Array<string>
  /** True when the provider never named a distinct outcome to verify. */
  unknownOutcome: boolean
}

/**
 * Where verification reads next. `exact` carries a durable
 * project/run/attempt identity; `discover` knows only a run id and must find
 * the durable run row (which alone carries a `report_identity`) before any
 * report read; `unverifiable` means no exact run identity exists and nothing
 * may be polled or linked.
 */
export type RunActionVerificationTarget =
  | { kind: 'exact'; identity: ObservatoryRunReportIdentity }
  | { kind: 'discover'; runId: string; projectId: string | null }
  | { kind: 'unverifiable'; reason: string }

const intentFor = (actionKind: RunActionKind): string =>
  actionKind === 'run.cancel' ? 'Cancel' : 'Retry'

/**
 * Resolve one action result to the exact durable run/report data verification
 * reads. Retry verifies the resulting (new) run; cancel verifies the target
 * run reached its terminal state. Only durable canonical identities
 * (`canonical_report` from the reconciliation seam, or a durable run row
 * discovered by exact run-id match) ever become a verification target.
 */
export function resolveVerificationTarget(
  result: RunActionResult,
  projectId: string | null,
): RunActionVerificationTarget {
  if (result.action_kind === 'run.cancel') {
    const runId = result.target?.run_id
    if (!runId || !runId.trim()) {
      return {
        kind: 'unverifiable',
        reason:
          'The action result names no target run, so no run evidence can be checked.',
      }
    }
    if (result.canonical_report) {
      return { kind: 'exact', identity: result.canonical_report }
    }
    return { kind: 'discover', runId, projectId }
  }

  if (result.canonical_report) {
    return { kind: 'exact', identity: result.canonical_report }
  }
  const resultingRun = result.resulting_run?.run_id
  if (resultingRun && resultingRun.trim()) {
    return { kind: 'discover', runId: resultingRun, projectId }
  }
  return {
    kind: 'unverifiable',
    reason:
      'The provider accepted the action without naming a distinct resulting run, so there is no exact run identity to verify against durable evidence.',
  }
}

/**
 * Classify one durable run report against the action's expected semantics.
 *
 * Only complete canonical evidence may classify as proven or failed:
 * `evidence_completeness === 'complete'`, an authoritative terminal outcome,
 * and no report gaps. Anything else — a missing report row, an incomplete
 * evidence profile, missing terminal outcome, or any report gap — stays
 * pending-incomplete with the gap named explicitly.
 *
 * Retry and cancel differ: retry is proven only by terminal `success` on the
 * new run; cancel is proven only by terminal `cancelled` on the target run.
 */
export function classifyRunActionEvidence(
  actionKind: RunActionKind,
  report: ObservatoryRunReport,
): RunActionVerification {
  const intent = intentFor(actionKind)
  const run = report.lifecycle?.run ?? null
  const terminal = report.terminal_outcome ?? null
  const gaps = (report.gaps ?? []).map((gap) => gap.field)
  const identity: ObservatoryRunReportIdentity = {
    project_id: report.project_id,
    run_id: report.run_id,
    attempt: report.attempt,
  }
  const completeness = run?.evidence_completeness ?? 'missing'

  if (completeness !== 'complete' || !terminal || gaps.length > 0) {
    const missing: Array<string> = []
    if (completeness !== 'complete') {
      missing.push(`evidence completeness is ${completeness}`)
    }
    if (!terminal) {
      missing.push('no authoritative terminal outcome yet')
    }
    if (gaps.length > 0) {
      missing.push(`report gaps: ${gaps.join(', ')}`)
    }
    return {
      state: 'pending-incomplete',
      headline: `${intent} not proven yet.`,
      detail: `Durable evidence is incomplete: ${missing.join('; ')}. Verification stays pending until complete canonical evidence exists; no success is claimed.`,
      identity,
      gaps,
      unknownOutcome: false,
    }
  }

  const status = terminal.status
  if (actionKind === 'run.retry') {
    if (status === 'success') {
      return {
        state: 'proven',
        headline:
          'Retry proven: durable evidence records the new run succeeded.',
        detail: `Complete canonical evidence for ${identity.project_id}/${identity.run_id}/attempt ${identity.attempt} records terminal outcome "${status}" with no gaps. Recovery is proven, not just accepted.`,
        identity,
        gaps,
        unknownOutcome: false,
      }
    }
    return {
      state: 'failed',
      headline: `Retry failed: durable evidence records terminal outcome "${status}".`,
      detail: `Complete canonical evidence for ${identity.project_id}/${identity.run_id}/attempt ${identity.attempt} ended in "${status}" instead of success. The failure is recorded; no success is claimed.`,
      identity,
      gaps,
      unknownOutcome: false,
    }
  }

  if (status === 'cancelled') {
    return {
      state: 'proven',
      headline:
        'Cancel proven: durable evidence records the run terminal as cancelled.',
      detail: `Complete canonical evidence for ${identity.project_id}/${identity.run_id}/attempt ${identity.attempt} records terminal outcome "${status}" with no gaps.`,
      identity,
      gaps,
      unknownOutcome: false,
    }
  }
  return {
    state: 'failed',
    headline: `Cancel not proven: the run ended as "${status}", not cancelled.`,
    detail: `Complete canonical evidence for ${identity.project_id}/${identity.run_id}/attempt ${identity.attempt} records terminal outcome "${status}". The run finished, but the cancellation did not produce the cancelled terminal state.`,
    identity,
    gaps,
    unknownOutcome: false,
  }
}

/** Durable run/report reads used by the bounded poller. Both are pure GET
 * lookups; nothing here can resubmit a mutation.
 */
export interface RunActionVerificationLookups {
  /** Durable run read model; null means the lookup failed (stays pending). */
  listRuns: () => Promise<Array<ObservatoryRun> | null>
  /** Exact attempt report; null means not found yet or unreadable. */
  getReport: (
    identity: ObservatoryRunReportIdentity,
  ) => Promise<ObservatoryRunReport | null>
}

export const RUN_ACTION_VERIFICATION_POLL_DELAY_MS = 5_000
export const RUN_ACTION_VERIFICATION_MAX_POLLS = 12

function pendingVerification(
  headline: string,
  detail: string,
  identity: ObservatoryRunReportIdentity | null,
): RunActionVerification {
  return {
    state: 'pending-incomplete',
    headline,
    detail,
    identity,
    gaps: [],
    unknownOutcome: false,
  }
}

/**
 * Match a durable run row to the run a verification target names. Only rows
 * that carry a durable `report_identity` can match — legacy, manifest, and
 * recovered-provider rows never receive one and are never promoted.
 */
export function findDurableReportIdentity(
  runs: Array<ObservatoryRun>,
  target: Extract<RunActionVerificationTarget, { kind: 'discover' }>,
): ObservatoryRunReportIdentity | null {
  for (const run of runs) {
    const identity = run.report_identity
    if (!identity) continue
    if (target.projectId && identity.project_id !== target.projectId) continue
    if (identity.run_id === target.runId || run.id === target.runId) {
      return identity
    }
  }
  return null
}

/**
 * Run bounded verify-after-action against durable run/report lookups.
 *
 * The poller resolves the exact durable identity (polling the durable run
 * read model when only a run id is known), then reads the exact attempt
 * report and classifies it. Any terminal classification (proven/failed)
 * stops the poller; otherwise it polls up to `maxPolls` times and ends in an
 * explicit pending-incomplete state. `onState` receives every state change.
 *
 * Returns a cancel function. After cancel, no further lookups happen and no
 * further states are emitted.
 */
export function startRunActionVerification(options: {
  actionKind: RunActionKind
  target: RunActionVerificationTarget
  lookups: RunActionVerificationLookups
  pollDelayMs?: number
  maxPolls?: number
  delay?: (ms: number) => Promise<void>
  onState: (verification: RunActionVerification) => void
  /** Called once when the poller stops on its own (terminal or bounded window). */
  onDone?: () => void
}): () => void {
  const {
    actionKind,
    target,
    lookups,
    pollDelayMs = RUN_ACTION_VERIFICATION_POLL_DELAY_MS,
    maxPolls = RUN_ACTION_VERIFICATION_MAX_POLLS,
    delay = (ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
    onState,
    onDone,
  } = options

  if (target.kind === 'unverifiable') {
    onState({
      state: 'pending-incomplete',
      headline: `${intentFor(actionKind)} cannot be verified yet.`,
      detail: `${target.reason} The action outcome above remains the only record; no report link is fabricated.`,
      identity: null,
      gaps: [],
      unknownOutcome: true,
    })
    onDone?.()
    return () => {}
  }

  let cancelled = false
  let identity: ObservatoryRunReportIdentity | null =
    target.kind === 'exact' ? target.identity : null

  void (async () => {
    for (let poll = 1; poll <= maxPolls; poll += 1) {
      if (!identity && target.kind === 'discover') {
        const runs = await lookups.listRuns()
        if (cancelled) return
        if (runs) {
          identity = findDurableReportIdentity(runs, target)
        }
      }
      if (identity) {
        const report = await lookups.getReport(identity)
        if (cancelled) return
        if (report) {
          const verification = classifyRunActionEvidence(actionKind, report)
          onState(verification)
          if (verification.state !== 'pending-incomplete') {
            onDone?.()
            return
          }
        }
      }
      if (poll < maxPolls) {
        await delay(pollDelayMs)
        if (cancelled) return
      }
    }
    onState(
      pendingVerification(
        `${intentFor(actionKind)} not proven yet.`,
        identity
          ? `Verification stopped after its bounded window: durable evidence for ${identity.project_id}/${identity.run_id}/attempt ${identity.attempt} is still absent or incomplete. The action outcome above remains the record of provider acceptance; no success is claimed and no report link is fabricated beyond this durable identity.`
          : 'Verification stopped after its bounded window without finding durable run evidence for the resulting run. The action outcome above remains the record of provider acceptance; no success is claimed and no report link is fabricated.',
        identity,
      ),
    )
    onDone?.()
  })()

  return () => {
    cancelled = true
  }
}
