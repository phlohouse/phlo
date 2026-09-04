/**
 * Canonical Dataset projection client for the Observatory.
 *
 * Observatory is a pure Dataset client: every surface renders
 * the projection `DatasetAuthority.projection()` serves — embedded as
 * `canonical` on the profile read model — and submits publish/retire
 * transitions that carry the exact observed state. Nothing here re-derives
 * eligibility, controls, or publication state: every helper either parses
 * the API projection, explains a transition from it, or classifies the
 * durable transition result the API returned.
 */
import type {
  CanonicalDatasetProjection,
  ObservatoryActionResult,
  ObservatoryDatasetProfile,
} from './types'

export type DatasetTransitionAction = 'publish' | 'retire'

/**
 * Parse one raw `canonical` projection payload defensively. Returns null for
 * anything that is not a well-formed projection so surfaces fall back to an
 * explicit "projection unavailable" state instead of inventing facts.
 */
export function parseCanonicalDatasetProjection(
  raw: unknown,
): CanonicalDatasetProjection | null {
  if (typeof raw !== 'object' || raw === null) return null
  const value = raw as Record<string, unknown>
  if (typeof value.dataset_id !== 'string' || !value.dataset_id) return null
  if (typeof value.table_id !== 'string') return null
  const readiness = value.readiness
  if (typeof readiness !== 'object' || readiness === null) return null
  const readinessValue = readiness as Record<string, unknown>
  if (!Array.isArray(readinessValue.reasons)) return null
  if (!Array.isArray(readinessValue.blockers)) return null
  if (!Array.isArray(readinessValue.warnings)) return null
  if (!Array.isArray(readinessValue.missing_evidence)) return null
  if (!Array.isArray(value.allowed_transitions)) return null
  return raw as CanonicalDatasetProjection
}

/** Extract the canonical projection from one profile read model. */
export function profileProjection(
  profile: ObservatoryDatasetProfile,
): CanonicalDatasetProjection | null {
  return parseCanonicalDatasetProjection(profile.canonical)
}

export function datasetTransitionActionId(
  datasetId: string,
  action: DatasetTransitionAction,
): string {
  return `dataset:${datasetId}:${action}`
}

/**
 * Explain one transition before execution (explain-then-execute). Every
 * field is read from the projection: the exact dataset identity, the exact
 * observed state the client will repeat back as the compare-and-set
 * version, the ordered canonical reasons, and whether the action is in the
 * projection's allowed set. No eligibility is computed here.
 */
export interface DatasetTransitionPlan {
  action: DatasetTransitionAction
  actionId: string
  datasetId: string
  /** Exact observed compare-and-set state from the projection. */
  expectedState: string | null
  /** Whether the projection's state machine allows this action right now. */
  allowed: boolean
  ready: boolean
  policyVersion: string | null
  lastActionId: string | null
  /** Canonical ordered reasons, verbatim from the projection. */
  reasons: Array<string>
}

export function buildDatasetTransitionPlan(
  projection: CanonicalDatasetProjection,
  action: DatasetTransitionAction,
): DatasetTransitionPlan {
  return {
    action,
    actionId: datasetTransitionActionId(projection.dataset_id, action),
    datasetId: projection.dataset_id,
    expectedState: projection.workflow_state ?? null,
    allowed: projection.allowed_transitions.includes(action),
    ready: projection.readiness.ready,
    policyVersion: projection.readiness.policy_version ?? null,
    lastActionId: projection.last_action_id ?? null,
    reasons: [...projection.readiness.reasons],
  }
}

export type DatasetTransitionOutcome =
  /** Applied and persisted; the reload reports the new durable state. */
  | 'committed'
  /** A committed action key replayed; the reload reports durable state. */
  | 'replayed'
  /** The record already sits in the target state; reload reports it. */
  | 'idempotent'
  /** Policy blocked the transition; nothing was written. */
  | 'blocked'
  /** State or identity conflict; nothing was written. */
  | 'conflict'
  | 'failed'
  | 'unknown'

export interface DatasetTransitionResultVerdict {
  outcome: DatasetTransitionOutcome
  /** Whether the result reports a persisted durable state to reload. */
  durable: boolean
  /** The API's verbatim message; surfaces render it without rewording. */
  message: string
}

/**
 * Classify one `/actions` result for a Dataset transition. The Observatory
 * API maps core outcomes onto succeeded/failed/skipped and appends a
 * "(replayed)"/"(idempotent)" suffix on replayed durability; this classifier
 * only makes that mapping explicit so no surface treats a blocked, unknown,
 * or conflicting result as a success. Callers always reload the durable
 * projection after any dataset transition result.
 */
export function classifyDatasetTransitionResult(
  result: ObservatoryActionResult,
): DatasetTransitionResultVerdict {
  const message = result.message
  if (result.status === 'succeeded') {
    if (message.endsWith('(replayed)')) {
      return { outcome: 'replayed', durable: true, message }
    }
    if (message.endsWith('(idempotent)')) {
      return { outcome: 'idempotent', durable: true, message }
    }
    return { outcome: 'committed', durable: true, message }
  }
  if (result.status === 'skipped') {
    return { outcome: 'blocked', durable: false, message }
  }
  if (result.status === 'failed') {
    return { outcome: 'conflict', durable: false, message }
  }
  return { outcome: 'unknown', durable: false, message }
}
