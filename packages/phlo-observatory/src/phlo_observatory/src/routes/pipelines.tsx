/**
 * /pipelines route. Dataset pipeline list ordered by freshness severity,
 * with the selection mirrored into ?pipelineId.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Clock3,
  PlayCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryAction,
  ObservatoryDatasetPipeline,
  ObservatoryPipelineStage,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import type { RunActionResult } from '@/observatory/api/runActions'
import type { RunActionVerification } from '@/observatory/api/runActionVerification'
import {
  cancelObservatoryRun,
  newRunActionIdempotencyKey,
  retryObservatoryRun,
} from '@/observatory/api/runActions'
import {
  getObservatoryPipelineRecords,
  getObservatoryRunRecords,
  getObservatoryRunReport,
} from '@/observatory/api/resources'
import {
  resolveVerificationTarget,
  startRunActionVerification,
} from '@/observatory/api/runActionVerification'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  invalidateCachedResources,
  useLiveResource,
} from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/pipelines')({
  component: Pipelines,
})

/** Resources a run action touches; invalidated so projections re-read. */
const RUN_ACTION_CACHE_KEYS = [
  'observatory:pipelines',
  'observatory:operations',
  'observatory:runs',
  'observatory:quality',
] as const

type RunActionDialogTarget = {
  pipeline: ObservatoryDatasetPipeline
  action: ObservatoryAction
}

/**
 * A contract action becomes a control only when the contract itself marks it
 * available: run.retry/run.cancel kind, enabled, and an exact run target.
 * A label alone never creates availability — capability-missing or ambiguous
 * actions stay informational so the UI cannot fake provider support.
 */
export function isRunActionControl(action: ObservatoryAction): boolean {
  return (
    (action.kind === 'run.retry' || action.kind === 'run.cancel') &&
    action.enabled &&
    typeof action.background_operation_id === 'string' &&
    action.background_operation_id.trim().length > 0
  )
}

/** Verification card tone per frozen verification state. */
const VERIFICATION_TONES: Record<
  RunActionVerification['state'],
  'ok' | 'warning' | 'error'
> = {
  proven: 'ok',
  'pending-incomplete': 'warning',
  failed: 'error',
}

/** Safe, human-renderable summary of one guarded run-action outcome. */
export function describeRunActionOutcome(result: RunActionResult): {
  tone: 'ok' | 'warning' | 'error'
  headline: string
  detail: string
} {
  const intent = result.action_kind === 'run.cancel' ? 'Cancel' : 'Retry'
  const handle = `Verification handle ${result.verification_handle}.`
  switch (result.status) {
    case 'accepted':
      return {
        tone: 'ok',
        headline: `${intent} accepted.`,
        detail: [
          result.resulting_run?.run_id
            ? `Resulting run ${result.resulting_run.run_id}.`
            : null,
          handle,
        ]
          .filter(Boolean)
          .join(' '),
      }
    case 'pending':
      return {
        tone: 'warning',
        headline: `${intent} pending reconciliation.`,
        detail:
          'The provider claimed success without naming a distinct resulting run. Durable run evidence will resolve the canonical report identity. ' +
          handle,
      }
    case 'reconciled':
      return {
        tone: 'ok',
        headline: `${intent} reconciled.`,
        detail: result.canonical_report
          ? `Canonical report identity ${result.canonical_report.project_id}/${result.canonical_report.run_id}/${result.canonical_report.attempt}. ${handle}`
          : handle,
      }
    case 'rejected':
      return {
        tone: 'error',
        headline: `${intent} rejected by the provider.`,
        detail: [result.message || 'The provider refused the action.', handle]
          .filter(Boolean)
          .join(' '),
      }
    case 'skipped':
      return {
        tone: 'warning',
        headline: `${intent} skipped.`,
        detail: `Nothing was executed (dry run). ${handle}`,
      }
  }
}

export function Pipelines() {
  const result = useLiveResource(
    getObservatoryPipelineRecords,
    120_000,
    'observatory:pipelines',
  )
  const pipelines = result.data ?? []
  const isLoading = result.isLoading
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const sortedPipelines = useMemo(
    () => [...pipelines].sort(comparePipelines),
    [pipelines],
  )
  const selected =
    sortedPipelines.find((pipeline) => pipelineKey(pipeline) === selectedId) ??
    sortedPipelines.find((pipeline) => pipeline.freshness_state === 'error') ??
    sortedPipelines.find(
      (pipeline) => pipeline.freshness_state === 'warning',
    ) ??
    sortedPipelines[0] ??
    null
  const counts = useMemo(() => countPipelines(pipelines), [pipelines])
  const [runAction, setRunAction] = useState<RunActionDialogTarget | null>(null)
  const selectPipeline = useCallback((pipelineId: string) => {
    setSelectedId(pipelineId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('pipelineId', pipelineId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'pipelineId',
    )
    if (!requested || requested === selectedId) return
    if (
      sortedPipelines.some((pipeline) => pipelineKey(pipeline) === requested)
    ) {
      setSelectedId(requested)
    }
  }, [selectedId, sortedPipelines])

  useEffect(() => {
    if (selectedId !== null || !selected) return
    setSelectedId(pipelineKey(selected))
  }, [selected, selectedId])

  return (
    <ObservatoryPage
      kicker="Operations"
      title="Pipelines"
      description="Recovery queue for Dataset freshness, failed stage evidence, supported actions, and linked runs."
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${pipelines.length} pipelines`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-pipeline-summary">
            <PipelineMetric
              icon={<CheckCircle2 className="size-4" />}
              label="Healthy"
              value={isLoading ? 'Loading' : counts.ok}
            />
            <PipelineMetric
              icon={<AlertCircle className="size-4" />}
              label="Blocked"
              value={isLoading ? 'Loading' : counts.error}
            />
            <PipelineMetric
              icon={<Clock3 className="size-4" />}
              label="Needs attention"
              value={isLoading ? 'Loading' : counts.warning + counts.unknown}
            />
            <PipelineMetric
              icon={<PlayCircle className="size-4" />}
              label="Actions ready"
              value={isLoading ? 'Loading' : counts.actionsReady}
            />
          </div>
          <div className="phlo-observatory-browser-toolbar">
            <div className="phlo-observatory-row-title">
              <Activity className="size-4" />
              Recovery queue
            </div>
          </div>
          {isLoading ? (
            <EmptyFlow
              detail="Reading live freshness, stage state, and action eligibility."
              title="Loading pipelines"
            />
          ) : result.error ? (
            <EmptyFlow detail={result.error} />
          ) : pipelines.length ? (
            <div className="phlo-observatory-pipeline-table" role="table">
              <div className="phlo-observatory-pipeline-head" role="row">
                <span>Dataset</span>
                <span>Freshness</span>
                <span>Stage evidence</span>
                <span>Next action</span>
                <span>Run evidence</span>
              </div>
              {sortedPipelines.map((pipeline, index) => (
                <PipelineRow
                  key={pipelineKey(pipeline) || `pipeline-${index}`}
                  onOpenRunAction={(action) =>
                    setRunAction({ action, pipeline })
                  }
                  onSelect={() => selectPipeline(pipelineKey(pipeline))}
                  pipeline={pipeline}
                  selected={pipelineKey(pipeline) === pipelineKey(selected)}
                />
              ))}
            </div>
          ) : (
            <EmptyFlow detail="No Dataset pipelines are available yet." />
          )}
        </div>
        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">
            Selected pipeline
          </div>
          {selected ? (
            <PipelineInspector
              onOpenRunAction={(action) =>
                setRunAction({ action, pipeline: selected })
              }
              pipeline={selected}
            />
          ) : (
            <>
              <h2>
                {isLoading
                  ? 'Loading pipeline evidence'
                  : 'No pipeline selected'}
              </h2>
              <p>
                {isLoading
                  ? 'Reading live freshness, stage state, and action eligibility.'
                  : 'Select a Dataset pipeline to inspect freshness, affected scope, and the next supported action.'}
              </p>
            </>
          )}
        </aside>
      </section>
      {runAction && (
        <RunActionDialog
          action={runAction.action}
          onClose={() => setRunAction(null)}
          pipeline={runAction.pipeline}
        />
      )}
    </ObservatoryPage>
  )
}

function PipelineRow({
  onOpenRunAction,
  onSelect,
  pipeline,
  selected,
}: {
  onOpenRunAction: (action: ObservatoryAction) => void
  onSelect: () => void
  pipeline: ObservatoryDatasetPipeline
  selected: boolean
}) {
  const dataset = pipeline.dataset
  const readyActions = pipeline.actions.filter((action) => action.enabled)
  const selectWithKeyboard = (event: React.KeyboardEvent) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onSelect()
  }
  return (
    <div
      className="phlo-observatory-pipeline-row"
      data-active={selected}
      data-state={pipeline.freshness_state}
      onClick={onSelect}
      onKeyDown={selectWithKeyboard}
      role="row"
      tabIndex={0}
    >
      <span
        className="phlo-observatory-dot"
        data-state={pipeline.freshness_state}
      />
      <div className="phlo-observatory-pipeline-dataset">
        <div className="phlo-observatory-row-title">
          <PlayCircle className="size-4" />
          <span>{dataset?.name ?? 'Unassigned pipeline'}</span>
        </div>
        <div className="phlo-observatory-row-meta">
          {pipeline.last_run?.label ?? fallbackPipelineDetail(pipeline)}
        </div>
      </div>
      <div className="phlo-observatory-pipeline-run">
        <span>{stateLabel(pipeline.freshness_state)}</span>
        <small>{pipeline.freshness_at ?? freshnessFallback(pipeline)}</small>
      </div>
      <div className="phlo-observatory-pipeline-stages">
        {pipeline.stages.map((stage) => (
          <span
            className="phlo-observatory-pipeline-stage"
            data-state={stage.state}
            key={stage.id}
          >
            <strong>{stage.label}</strong>
            <small>{stageEvidenceLabel(stage)}</small>
          </span>
        ))}
      </div>
      <div className="phlo-observatory-pipeline-actions">
        {readyActions.length > 0
          ? readyActions.map((action) =>
              isRunActionControl(action) ? (
                <button
                  className="phlo-observatory-pipeline-action"
                  data-enabled="true"
                  key={action.id}
                  onClick={(event) => {
                    event.stopPropagation()
                    onOpenRunAction(action)
                  }}
                  title={`${action.label} run ${action.background_operation_id ?? ''}`.trim()}
                  type="button"
                >
                  {action.label}
                </button>
              ) : (
                <span
                  className="phlo-observatory-pipeline-action"
                  data-enabled={action.enabled}
                  key={action.id}
                >
                  {action.label}
                </span>
              ),
            )
          : nextActionFallback(pipeline)}
      </div>
      <span>{pipeline.last_run?.id ?? runEvidenceFallback(pipeline)}</span>
    </div>
  )
}

function PipelineInspector({
  onOpenRunAction,
  pipeline,
}: {
  onOpenRunAction: (action: ObservatoryAction) => void
  pipeline: ObservatoryDatasetPipeline
}) {
  const dataset = pipeline.dataset
  return (
    <>
      <h2>{dataset?.name ?? 'Pipeline'}</h2>
      <p>{pipelineSummary(pipeline)}</p>
      <div className="phlo-observatory-pipeline-recovery-strip">
        <div>
          <span className="phlo-observatory-inspector-label">
            Why it matters
          </span>
          <strong>{recoveryImpact(pipeline)}</strong>
        </div>
        <div>
          <span className="phlo-observatory-inspector-label">Next action</span>
          <strong>{primaryActionLabel(pipeline)}</strong>
        </div>
      </div>
      <div className="phlo-observatory-detail-list">
        {dataset && (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            params={{ datasetId: dataset.id }}
            to="/datasets/$datasetId"
          >
            <span>Open Dataset</span>
            <small>
              {[dataset.publication_state, dataset.readiness_state]
                .filter(Boolean)
                .join(' · ')}
            </small>
          </Link>
        )}
        {pipeline.last_run && (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            search={{ operationId: pipeline.last_run.id }}
            to="/operations"
          >
            <span>Open recovery run</span>
            <small>{pipeline.last_run.label}</small>
          </Link>
        )}
        {dataset && (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            to="/quality"
          >
            <span>Review quality evidence</span>
            <small>Checks and failure context for this Dataset</small>
          </Link>
        )}
      </div>
      <div className="phlo-observatory-inspector-label">Stage evidence</div>
      <div className="phlo-observatory-detail-list">
        {pipeline.stages.map((stage) => (
          <div
            className="phlo-observatory-mini-row"
            data-state={stage.state}
            key={stage.id}
          >
            <span>{stage.label}</span>
            <small>
              {[
                stateLabel(stage.state),
                stage.resource?.label ?? stageEvidenceLabel(stage),
              ]
                .filter(Boolean)
                .join(' · ')}
            </small>
          </div>
        ))}
      </div>
      <div className="phlo-observatory-inspector-label">Action eligibility</div>
      <div className="phlo-observatory-detail-list">
        {pipeline.actions.map((action) => (
          <div
            className="phlo-observatory-mini-row phlo-observatory-pipeline-action-row"
            data-state={action.enabled ? 'ok' : 'unknown'}
            key={action.id}
          >
            <span>
              {isRunActionControl(action) ? (
                <button
                  className="phlo-observatory-pipeline-action"
                  data-enabled="true"
                  onClick={() => onOpenRunAction(action)}
                  type="button"
                >
                  {action.label} run
                </button>
              ) : (
                action.label
              )}
            </span>
            <small>
              {action.enabled
                ? [
                    'available now',
                    action.risk_level ? `${action.risk_level} risk` : null,
                    action.background_operation_id
                      ? `tracks ${action.background_operation_id}`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')
                : actionReasonLabel(action.reason)}
            </small>
          </div>
        ))}
      </div>
    </>
  )
}

/**
 * Pipeline-local explain > confirm > act > verify dialog for one guarded run
 * action. The explain pane renders the contract's guard metadata (capability,
 * permission, risk, confirmation, exact target run, expected evidence); the
 * confirm button submits dry_run=false exactly once per intent under a stable
 * idempotency key, and the outcome pane renders the normalized RunActionResult.
 */
export function RunActionDialog({
  action,
  onClose,
  pipeline,
}: {
  action: ObservatoryAction
  onClose: () => void
  pipeline: ObservatoryDatasetPipeline
}) {
  const runId = action.background_operation_id ?? ''
  const [idempotencyKey] = useState(() => newRunActionIdempotencyKey())
  const [submitting, setSubmitting] = useState(false)
  const [outcome, setOutcome] =
    useState<ObservatoryResourceResult<RunActionResult> | null>(null)
  // Bounded verify-after-action: after a guarded result, poll durable
  // run/report reads until complete canonical evidence proves or refutes
  // recovery. Verification never resubmits the mutation and is cancellable by
  // the operator or when the dialog closes.
  const [verification, setVerification] =
    useState<RunActionVerification | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [verificationStopped, setVerificationStopped] = useState(false)
  const cancelVerificationRef = useRef<(() => void) | null>(null)

  useEffect(() => () => cancelVerificationRef.current?.(), [])

  const stopVerification = () => {
    cancelVerificationRef.current?.()
    cancelVerificationRef.current = null
    setVerifying(false)
    setVerificationStopped(true)
  }

  const startVerification = (resultData: RunActionResult) => {
    cancelVerificationRef.current?.()
    cancelVerificationRef.current = null
    if (resultData.status === 'rejected' || resultData.status === 'skipped') {
      // The provider refused or nothing executed: there is no outcome claim
      // to verify against durable evidence.
      setVerification(null)
      setVerifying(false)
      return
    }
    setVerification(null)
    setVerificationStopped(false)
    setVerifying(true)
    cancelVerificationRef.current = startRunActionVerification({
      actionKind: resultData.action_kind,
      target: resolveVerificationTarget(
        resultData,
        pipeline.dataset?.id ?? null,
      ),
      lookups: {
        listRuns: async () => (await getObservatoryRunRecords()).data,
        getReport: async (identity) =>
          (await getObservatoryRunReport({ data: identity })).data,
      },
      onState: setVerification,
      onDone: () => {
        cancelVerificationRef.current = null
        setVerifying(false)
      },
    })
  }

  useEffect(() => {
    if (typeof window === 'undefined') return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const confirm = () => {
    if (submitting) return
    setSubmitting(true)
    const request =
      action.kind === 'run.cancel' ? cancelObservatoryRun : retryObservatoryRun
    void request({
      data: {
        idempotencyKey,
        projectId: pipeline.dataset?.id ?? null,
        runId,
      },
    })
      .then((next) => {
        setOutcome(next)
        invalidateCachedResources([...RUN_ACTION_CACHE_KEYS])
        if (next.data) startVerification(next.data)
        if (typeof window !== 'undefined') {
          // The live-resource hooks refresh on focus; nudge mounted
          // Pipelines/Runs/Operations readers to re-read their projections.
          window.dispatchEvent(new Event('focus'))
        }
      })
      .finally(() => setSubmitting(false))
  }

  const outcomeSummary = outcome?.data
    ? describeRunActionOutcome(outcome.data)
    : null
  const canonical = outcome?.data?.canonical_report ?? null
  // A transport failure keeps the intent open: resubmitting reuses the same
  // idempotency key, so the durable claim store replays instead of
  // re-invoking the provider. A real guarded result closes the intent.
  const submitted = outcome !== null && !outcome.error
  // Verify-after-action applies to results that claim an outcome: accepted,
  // pending, and reconciled results must be proven from durable evidence
  // before any success is claimed. Rejected and skipped results name no
  // outcome claim to verify.
  const needsVerification =
    outcome?.data !== null &&
    outcome?.data !== undefined &&
    (outcome.data.status === 'accepted' ||
      outcome.data.status === 'pending' ||
      outcome.data.status === 'reconciled')

  return (
    <div
      aria-label={`${action.label} run`}
      aria-modal="true"
      className="phlo-observatory-command-overlay"
      role="dialog"
    >
      <button
        aria-label="Close dialog"
        className="phlo-observatory-command-backdrop"
        onClick={onClose}
        type="button"
      />
      <div className="phlo-observatory-search-popover">
        <div className="phlo-observatory-workspace-toolbar">
          <span className="phlo-observatory-row-title">{action.label} run</span>
          <span className="phlo-observatory-pill">
            {action.risk_level} risk
          </span>
        </div>
        <p>
          Guarded orchestration action for run <strong>{runId}</strong> on{' '}
          {pipeline.dataset?.name ?? 'this pipeline'}. Review the guard evidence
          before confirming; the request is idempotent, so resubmitting the same
          intent can never double-invoke the provider.
        </p>
        <dl className="phlo-observatory-facts">
          <Fact
            label="Capability"
            value={action.required_capability ?? 'not reported'}
          />
          <Fact
            label="Permission"
            value={action.required_permission ?? 'not reported'}
          />
          <Fact
            label="Confirmation"
            value={action.requires_confirmation ? 'required' : 'not required'}
          />
          <Fact label="Target run" value={runId} />
        </dl>
        <div className="phlo-observatory-detail-list">
          <div className="phlo-observatory-mini-row">
            <span>Expected evidence</span>
            <small>{action.expected_evidence.join(' · ')}</small>
          </div>
          <div className="phlo-observatory-mini-row">
            <span>Idempotency key</span>
            <small>{idempotencyKey}</small>
          </div>
        </div>
        {outcomeSummary && outcome?.data && (
          <div
            className="phlo-observatory-operation-recovery-card"
            data-state={outcomeSummary.tone}
          >
            <span>Outcome</span>
            <strong>{outcomeSummary.headline}</strong>
            <small>{outcomeSummary.detail}</small>
            {outcome.data.message && <small>{outcome.data.message}</small>}
            {canonical && (
              <Link
                params={{
                  attempt: String(canonical.attempt),
                  projectId: canonical.project_id,
                  runId: canonical.run_id,
                }}
                to="/runs/$projectId/$runId/attempts/$attempt/report"
              >
                Open canonical run report
              </Link>
            )}
          </div>
        )}
        {needsVerification && (
          <div
            className="phlo-observatory-operation-recovery-card"
            data-state={
              verification ? VERIFICATION_TONES[verification.state] : 'warning'
            }
          >
            <span>Verification</span>
            {verification ? (
              <>
                <strong>{verification.headline}</strong>
                <small>{verification.detail}</small>
                {verification.identity && (
                  <Link
                    params={{
                      attempt: String(verification.identity.attempt),
                      projectId: verification.identity.project_id,
                      runId: verification.identity.run_id,
                    }}
                    to="/runs/$projectId/$runId/attempts/$attempt/report"
                  >
                    Open canonical run report
                  </Link>
                )}
              </>
            ) : (
              <strong>Checking durable run evidence…</strong>
            )}
            {verifying && (
              <button onClick={stopVerification} type="button">
                Stop verifying
              </button>
            )}
            {verificationStopped && (
              <small>
                Verification stopped before complete evidence arrived. The
                action outcome above remains the record; nothing is claimed as
                proven.
              </small>
            )}
          </div>
        )}
        {outcome?.error && (
          <div className="phlo-observatory-failure-callout">
            <strong>Action could not be completed</strong>
            <span>{outcome.error}</span>
          </div>
        )}
        <div className="phlo-observatory-action-row">
          <button
            disabled={submitting || submitted}
            onClick={confirm}
            type="button"
          >
            {submitting
              ? 'Submitting…'
              : submitted
                ? 'Submitted'
                : outcome?.error
                  ? 'Retry submission'
                  : `Confirm ${action.label.toLowerCase()}`}
          </button>
          <button onClick={onClose} type="button">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

function Fact({
  label,
  value,
}: {
  label: string
  value: string | number | boolean | null
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value === null || value === '' ? 'not reported' : String(value)}</dd>
    </>
  )
}

function pipelineSummary(pipeline: ObservatoryDatasetPipeline): string {
  const timestamp = pipeline.freshness_at
    ? `Evidence timestamp ${pipeline.freshness_at}.`
    : freshnessFallback(pipeline)
  return `${stateLabel(pipeline.freshness_state)} freshness. ${timestamp}`
}

function recoveryImpact(pipeline: ObservatoryDatasetPipeline): string {
  if (pipeline.freshness_state === 'error') {
    return 'Publication and trust are blocked until the failed refresh is recovered.'
  }
  if (pipeline.freshness_state === 'warning') {
    return 'Freshness is drifting; review before this Dataset becomes blocked.'
  }
  if (pipeline.freshness_state === 'ok') {
    return 'Refresh evidence is current and no recovery is required.'
  }
  return 'Freshness cannot be proven yet; check stage evidence before relying on this Dataset.'
}

function primaryActionLabel(pipeline: ObservatoryDatasetPipeline): string {
  const action = pipeline.actions.find((candidate) => candidate.enabled)
  if (action) return action.label
  return nextActionFallback(pipeline)
}

function fallbackPipelineDetail(pipeline: ObservatoryDatasetPipeline): string {
  if (pipeline.dataset) return 'No run evidence reported'
  return 'Candidate table without Dataset ownership'
}

function freshnessFallback(pipeline: ObservatoryDatasetPipeline): string {
  if (pipeline.last_run) return 'Freshness timestamp not reported'
  return 'No run evidence reported'
}

function runEvidenceFallback(pipeline: ObservatoryDatasetPipeline): string {
  if (pipeline.dataset) return 'No run evidence'
  return 'No Dataset ownership'
}

function nextActionFallback(pipeline: ObservatoryDatasetPipeline): string {
  if (pipeline.freshness_state === 'ok') return 'No recovery needed'
  if (!pipeline.last_run) return 'No run evidence'
  const disabledReason = pipeline.actions.find(
    (action) => action.reason,
  )?.reason
  if (disabledReason) return actionReasonLabel(disabledReason)
  return 'No action eligible'
}

function actionReasonLabel(reason: string | null | undefined): string {
  if (!reason) return 'Not available'
  return reason
    .replace(
      /requires an orchestrator operation provider\.?/gi,
      'is not currently supported for this Dataset.',
    )
    .replace(
      /requires partition-aware orchestrator support\.?/gi,
      'requires partition-aware run support.',
    )
}

function stageEvidenceLabel(stage: ObservatoryPipelineStage): string {
  if (stage.state === 'unknown') return 'not reported'
  return stateLabel(stage.state)
}

function stateLabel(state: string): string {
  return state.replace(/_/g, ' ')
}

function PipelineMetric({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: string | number
}) {
  return (
    <div className="phlo-observatory-pipeline-summary-cell">
      <span>
        {icon}
        {label}
      </span>
      <strong>{value}</strong>
    </div>
  )
}

function pipelineKey(pipeline: ObservatoryDatasetPipeline | null): string {
  if (!pipeline) return ''
  return (
    pipeline.dataset?.id ?? pipeline.last_run?.id ?? pipeline.freshness_at ?? ''
  )
}

function countPipelines(pipelines: Array<ObservatoryDatasetPipeline>): {
  ok: number
  warning: number
  error: number
  unknown: number
  actionsReady: number
} {
  return pipelines.reduce(
    (counts, pipeline) => {
      if (pipeline.freshness_state === 'ok') counts.ok += 1
      else if (pipeline.freshness_state === 'warning') counts.warning += 1
      else if (pipeline.freshness_state === 'error') counts.error += 1
      else counts.unknown += 1
      counts.actionsReady += pipeline.actions.filter(
        (action) => action.enabled,
      ).length
      return counts
    },
    { ok: 0, warning: 0, error: 0, unknown: 0, actionsReady: 0 },
  )
}

function comparePipelines(
  left: ObservatoryDatasetPipeline,
  right: ObservatoryDatasetPipeline,
): number {
  return pipelineScore(right) - pipelineScore(left)
}

function pipelineScore(pipeline: ObservatoryDatasetPipeline): number {
  const freshnessRank: Record<string, number> = {
    error: 400,
    warning: 300,
    unknown: 200,
    ok: 100,
  }
  const time = Date.parse(pipeline.freshness_at ?? '')
  const stateScore =
    (freshnessRank[pipeline.freshness_state] ?? 0) * 1_000_000_000
  const actionScore =
    pipeline.actions.filter((action) => action.enabled).length * 1_000_000
  return stateScore + actionScore + (Number.isNaN(time) ? 0 : time / 1_000)
}

function EmptyFlow({
  detail,
  title = 'No Dataset pipeline available',
}: {
  detail: string
  title?: string
}) {
  return (
    <div className="phlo-observatory-operation-empty">
      <div>
        <span className="phlo-observatory-inspector-label">Pipelines</span>
        <h2>{title}</h2>
        <p>{detail}</p>
      </div>
    </div>
  )
}
