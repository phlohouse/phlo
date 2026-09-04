/**
 * Guarded run-action clients for retry and cancel controls.
 *
 * One typed client per guarded endpoint from the run-action contract
 * (packages/phlo-api/.../run_action_contract.py): POST /runs/{run_id}/retry and
 * POST /runs/{run_id}/cancel on phlo-api. Both are authenticated mutations:
 * the shared inbound mutation-authorization middleware forwards the signed-in
 * human's bearer credential and nothing else. Every submission sends
 * dry_run=false plus a mandatory non-blank idempotency key, so a replayed
 * intent answers from the persisted claim store with a byte-identical result.
 */
import { createServerFn } from '@tanstack/react-start'

import type {
  ObservatoryMetadata,
  ObservatoryResourceResult,
  ObservatoryRunReportIdentity,
} from './types'
import { apiPost } from '@/server/phlo-api'
import { mutationAuthorization } from '@/server/authenticated-mutation'

const Observatory_API_PREFIX = '/api/observatory'

export type RunActionKind = 'run.retry' | 'run.cancel'

export type RunActionStatus =
  | 'accepted'
  | 'pending'
  | 'reconciled'
  | 'rejected'
  | 'skipped'

export interface RunActionIdentity {
  run_id: string
  project_id?: string | null
  attempt?: number | null
}

/**
 * Mirror of the backend RunActionResult (contract_version 1).
 */
export interface RunActionResult {
  contract_version: number
  action_kind: RunActionKind
  status: RunActionStatus
  verification_handle: string
  target: RunActionIdentity
  resulting_run?: RunActionIdentity | null
  canonical_report?: ObservatoryRunReportIdentity | null
  canonical_report_path?: string | null
  provider?: ObservatoryMetadata
  message: string
}

export interface RunActionClientInput {
  runId: string
  idempotencyKey: string
  projectId?: string | null
}

const RUN_ACTION_TIMEOUT_MS = 130_000

function apiUnavailable<T>(error: unknown): ObservatoryResourceResult<T> {
  return {
    data: null,
    error:
      error instanceof Error ? error.message : 'Lakehouse API is unavailable',
  }
}

/**
 * One idempotency key per dialog intent. Generated once when the confirm
 * dialog opens and reused for every retry of the same submission, so a
 * network replay can never double-invoke the provider.
 */
export function newRunActionIdempotencyKey(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return `run-action-${crypto.randomUUID()}`
  }
  return `run-action-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export const retryObservatoryRun = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator((input: RunActionClientInput) => input)
  .handler(
    async ({
      data,
      context,
    }): Promise<ObservatoryResourceResult<RunActionResult>> => {
      try {
        const result = await apiPost<RunActionResult>(
          `${Observatory_API_PREFIX}/runs/${encodeURIComponent(data.runId)}/retry`,
          {
            dry_run: false,
            idempotency_key: data.idempotencyKey,
            project_id: data.projectId ?? null,
          },
          RUN_ACTION_TIMEOUT_MS,
          context.authorization,
        )
        return { data: result, error: null }
      } catch (error) {
        return apiUnavailable<RunActionResult>(error)
      }
    },
  )

export const cancelObservatoryRun = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator((input: RunActionClientInput) => input)
  .handler(
    async ({
      data,
      context,
    }): Promise<ObservatoryResourceResult<RunActionResult>> => {
      try {
        const result = await apiPost<RunActionResult>(
          `${Observatory_API_PREFIX}/runs/${encodeURIComponent(data.runId)}/cancel`,
          {
            idempotency_key: data.idempotencyKey,
            project_id: data.projectId ?? null,
          },
          RUN_ACTION_TIMEOUT_MS,
          context.authorization,
        )
        return { data: result, error: null }
      } catch (error) {
        return apiUnavailable<RunActionResult>(error)
      }
    },
  )
