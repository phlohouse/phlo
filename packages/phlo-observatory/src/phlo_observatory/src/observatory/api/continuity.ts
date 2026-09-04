/**
 * Typed clients for the guarded continuity action API.
 *
 * One typed client per landed endpoint on phlo-api
 * (packages/phlo-api/src/phlo_api/api/continuity.py): GET /api/continuity/operations,
 * POST /api/continuity/plan (read-only, immutable target-bound dry-run plan),
 * one guarded POST /api/continuity/apply (authenticated, authorized, confirmed,
 * durable-idempotent through the operation journal), and the canonical
 * restart-safe GET /api/continuity/verifications/{operation_id}.
 *
 * There are no backend fallbacks: the shapes below mirror the API contract
 * exactly, transport failures surface as error strings, and nothing
 * here invents a capability the API does not report. Supported operations come
 * only from the backend inventory; destructive orphan deletion is never
 * claimable from this surface.
 */
import { createServerFn } from '@tanstack/react-start'

import type { ObservatoryResourceResult } from './types'
import { apiGet, apiPost } from '@/server/phlo-api'
import { mutationAuthorization } from '@/server/authenticated-mutation'

const CONTINUITY_API_PREFIX = '/api/continuity'

const CONTINUITY_TIMEOUT_MS = 130_000
const CONTINUITY_READ_TIMEOUT_MS = 30_000

/** One entry of the backend operation inventory (read-only). */
export interface ContinuityOperationDescriptor {
  operation: string
  family: string
  surface: 'plan' | 'apply'
  requires_confirmation: boolean
  description: string
}

/** An operation the backend explicitly refuses (e.g. orphan_delete). */
export interface ContinuityUnsupportedOperation {
  operation: string
  family: string
  reason: string
}

export interface ContinuityOperationsInventory {
  operations: Array<ContinuityOperationDescriptor>
  unsupported: Array<ContinuityUnsupportedOperation>
}

/** Explicit, located restore destination bound into every plan. */
export interface ContinuityPlanTarget {
  target_id: string
  location: string
}

/** Mirror of the backend RestorePlan.to_dict() payload. */
export interface ContinuityRestorePlan {
  schema_version: string
  plan_token: string
  backup_set_dir: string
  backup_set_id: string
  set_digest: string
  target: ContinuityPlanTarget
  provider_order: Array<string>
  created_at: string
  expires_at: string
}

/** Mirror of the backend UpgradePlan.to_dict() payload. */
export interface ContinuityUpgradePlan {
  schema_version: string
  plan_token: string
  from_version: string
  to_version: string
  backup_set_dir: string
  backup_set_id: string
  backup_digest: string
  migration_digest: string
  target: ContinuityPlanTarget
  created_at: string
  expires_at: string
}

/** The backend maintenance plan is a provider-planned dict with a token. */
export interface ContinuityMaintenancePlan {
  operation: string
  table_name: string
  ref: string
  plan_token: string
}

export type ContinuityPlan = (
  | { kind: 'restore'; plan: ContinuityRestorePlan }
  | { kind: 'upgrade'; plan: ContinuityUpgradePlan }
  | { kind: 'maintenance'; plan: ContinuityMaintenancePlan }
) & {
  /** Immutable plan token; the apply confirmation must equal this value. */
  planToken: string
  /** Canonical durable journal handle for the apply. */
  operationId: string
}

interface ContinuityPlanResponse {
  operation: string
  plan:
    | ContinuityRestorePlan
    | ContinuityUpgradePlan
    | ContinuityMaintenancePlan
  plan_token: string
  operation_id: string
}

export type ContinuityPlanRequest =
  | { operation: 'restore.plan'; backupSet: string; target: string }
  | {
      operation: 'upgrade.plan'
      backupSet: string
      target: string
      fromVersion: string
      toVersion: string
    }
  | {
      operation: 'maintenance.plan'
      maintenanceOperation: string
      table: string
      ref: string
    }

export type ContinuityApplyRequest =
  | { operation: 'backup.create'; idempotencyKey: string; target: string }
  | {
      operation: 'restore.apply'
      idempotencyKey: string
      plan: ContinuityRestorePlan
      confirmationToken: string
    }
  | {
      operation: 'upgrade.apply'
      idempotencyKey: string
      plan: ContinuityUpgradePlan
      confirmationToken: string
    }
  | {
      operation: 'maintenance.apply'
      idempotencyKey: string
      plan: ContinuityMaintenancePlan
      confirmationToken: string
      table: string
      ref: string
    }

/**
 * Union of the landed apply result payloads (backup set, restore, upgrade,
 * maintenance). Every variant carries the canonical `operation_id` used for
 * verification; evidence fields vary per family.
 */
export interface ContinuityApplyResult {
  operation: string
  operation_id: string
  /** Journal state reported by the core service, when present. */
  state?: string
  /** Backup/restore acceptance evidence. */
  accepted?: boolean
  set_id?: string
  target?: string
  /** Maintenance completion evidence. */
  status?: string
  plan_token?: string
  from_version?: string
  to_version?: string
  steps?: Array<Record<string, NonNullable<unknown>>>
  reconciliation?: {
    ok: boolean
    checks: Record<string, boolean>
    reasons: Array<string>
  } | null
  rollback_action?: string | null
  forward_repair?: Record<string, NonNullable<unknown>> | null
  failure?: Record<string, NonNullable<unknown>> | null
}

/** Mirror of the durable operation journal entry (verification lookup). */
export interface ContinuityVerificationEntry {
  operation_id: string
  subject: string
  action: string
  target: string
  plan_token: string
  state: 'claimed' | 'submitted' | 'succeeded' | 'failed' | 'unknown'
  claim_expiry: string
  result: Record<string, NonNullable<unknown>> | null
  observation_time: string
  /** True when a post-submission outcome is unknown and replay is blocked. */
  replay_blocked: boolean
}

function apiUnavailable<T>(error: unknown): ObservatoryResourceResult<T> {
  return {
    data: null,
    error:
      error instanceof Error ? error.message : 'Lakehouse API is unavailable',
  }
}

/**
 * Extract the stable backend error code from a failed call. phlo-api errors
 * arrive as `phlo-api error: {status} {body}` where body carries
 * `{"detail": {"error": code, ...}}`; the UI renders that stable code instead
 * of an unstructured message.
 */
export function parseContinuityApiError(error: unknown): {
  status: number
  code: string
} | null {
  if (!(error instanceof Error)) return null
  const match = /^phlo-api error: (\d+) (.*)$/s.exec(error.message)
  if (!match) return null
  try {
    const payload = JSON.parse(match[2]) as {
      detail?: { error?: string } | string
    }
    const detail = payload.detail
    if (typeof detail === 'object' && detail !== null && detail.error) {
      return { status: Number(match[1]), code: detail.error }
    }
    return { status: Number(match[1]), code: 'unknown_error' }
  } catch {
    return { status: Number(match[1]), code: 'unknown_error' }
  }
}

/**
 * Read-only inventory of supported and explicitly unsupported continuity
 * operations. This is the only source of what this surface may offer.
 */
export const getContinuityOperations = createServerFn().handler(
  async (): Promise<
    ObservatoryResourceResult<ContinuityOperationsInventory>
  > => {
    try {
      const data = await apiGet<ContinuityOperationsInventory>(
        `${CONTINUITY_API_PREFIX}/operations`,
        undefined,
        CONTINUITY_READ_TIMEOUT_MS,
      )
      return { data, error: null }
    } catch (error) {
      return apiUnavailable<ContinuityOperationsInventory>(error)
    }
  },
)

/**
 * Read-only, mutation-free dry-run plan. The response is an immutable,
 * target-bound plan with a deterministic token; the exact token must come
 * back as the apply confirmation.
 */
export const planContinuityOperation = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator((input: ContinuityPlanRequest) => input)
  .handler(
    async ({
      data,
      context,
    }): Promise<ObservatoryResourceResult<ContinuityPlan>> => {
      try {
        const body =
          data.operation === 'restore.plan'
            ? {
                operation: data.operation,
                backup_set: data.backupSet,
                target: data.target,
              }
            : data.operation === 'upgrade.plan'
              ? {
                  operation: data.operation,
                  backup_set: data.backupSet,
                  target: data.target,
                  from_version: data.fromVersion,
                  to_version: data.toVersion,
                }
              : {
                  operation: data.operation,
                  maintenance_operation: data.maintenanceOperation,
                  table: data.table,
                  ref: data.ref,
                }
        const response = await apiPost<ContinuityPlanResponse>(
          `${CONTINUITY_API_PREFIX}/plan`,
          body,
          CONTINUITY_READ_TIMEOUT_MS,
          context.authorization,
        )
        let planned: ContinuityPlan
        if (response.operation.startsWith('restore.')) {
          planned = {
            kind: 'restore',
            plan: response.plan as ContinuityRestorePlan,
            planToken: response.plan_token,
            operationId: response.operation_id,
          }
        } else if (response.operation.startsWith('upgrade.')) {
          planned = {
            kind: 'upgrade',
            plan: response.plan as ContinuityUpgradePlan,
            planToken: response.plan_token,
            operationId: response.operation_id,
          }
        } else {
          planned = {
            kind: 'maintenance',
            plan: response.plan as ContinuityMaintenancePlan,
            planToken: response.plan_token,
            operationId: response.operation_id,
          }
        }
        return { data: planned, error: null }
      } catch (error) {
        return apiUnavailable<ContinuityPlan>(error)
      }
    },
  )

/**
 * One guarded apply endpoint client. Every submission carries a mandatory
 * non-blank idempotency key; confirmed operations carry the exact reviewed
 * plan token. A replayed intent answers from the durable journal with a
 * byte-identical result.
 */
export const applyContinuityOperation = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator((input: ContinuityApplyRequest) => input)
  .handler(
    async ({
      data,
      context,
    }): Promise<ObservatoryResourceResult<ContinuityApplyResult>> => {
      try {
        let body: Record<string, NonNullable<unknown>>
        if (data.operation === 'backup.create') {
          body = {
            operation: data.operation,
            idempotency_key: data.idempotencyKey,
            target: data.target,
          }
        } else if (data.operation === 'maintenance.apply') {
          body = {
            operation: data.operation,
            idempotency_key: data.idempotencyKey,
            plan: data.plan,
            confirmation_token: data.confirmationToken,
            table: data.table,
            ref: data.ref,
          }
        } else {
          body = {
            operation: data.operation,
            idempotency_key: data.idempotencyKey,
            plan: data.plan,
            confirmation_token: data.confirmationToken,
          }
        }
        const result = await apiPost<ContinuityApplyResult>(
          `${CONTINUITY_API_PREFIX}/apply`,
          body,
          CONTINUITY_TIMEOUT_MS,
          context.authorization,
        )
        return { data: result, error: null }
      } catch (error) {
        return apiUnavailable<ContinuityApplyResult>(error)
      }
    },
  )

/**
 * Canonical, restart-safe verification lookup. Pure GET: it can never
 * resubmit the underlying mutation.
 */
export const getContinuityVerification = createServerFn()
  .inputValidator((input: { operationId: string }) => input)
  .handler(
    async ({
      data,
    }): Promise<ObservatoryResourceResult<ContinuityVerificationEntry>> => {
      try {
        // The operation id is a canonical server-derived handle (it may
        // contain path separators); the backend mounts this route with a
        // catch-all path converter, so it is appended verbatim.
        const entry = await apiGet<ContinuityVerificationEntry>(
          `${CONTINUITY_API_PREFIX}/verifications/${data.operationId}`,
          undefined,
          CONTINUITY_READ_TIMEOUT_MS,
        )
        return { data: entry, error: null }
      } catch (error) {
        return apiUnavailable<ContinuityVerificationEntry>(error)
      }
    },
  )

/**
 * One idempotency key per apply intent. Generated once per reviewed plan and
 * reused for every resubmission of the same intent, so a replayed request
 * answers from the durable journal instead of re-invoking the provider.
 */
export function newContinuityIdempotencyKey(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return `continuity-${crypto.randomUUID()}`
  }
  return `continuity-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2)}`
}
