/**
 * /pipelines route. Dataset pipeline list ordered by freshness severity,
 * with the selection mirrored into ?pipelineId.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { AlertCircle, CheckCircle2, Clock3, PlayCircle } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'

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
import {
  invalidateCachedResources,
  useLiveResource,
} from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { HealthDot, StatusBadge } from '@/components/observatory/status'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

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

const TONE_CLASS: Record<'ok' | 'warning' | 'error', string> = {
  ok: 'border-status-ok/40 bg-status-ok/5',
  warning: 'border-status-warning/40 bg-status-warning/5',
  error: 'border-status-error/40 bg-status-error/5',
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
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading ? 'Loading' : `${pipelines.length} pipelines`}
          </Badge>
        }
        description="Recovery queue for Dataset freshness, failed stage evidence, supported actions, and linked runs."
        title="Pipelines"
      />
      <StatGrid>
        <StatCard
          icon={<CheckCircle2 className="size-3.5" />}
          label="Healthy"
          value={isLoading ? 'Loading' : counts.ok}
        />
        <StatCard
          icon={<AlertCircle className="size-3.5" />}
          label="Blocked"
          state={counts.error > 0 ? 'error' : 'ok'}
          value={isLoading ? 'Loading' : counts.error}
        />
        <StatCard
          icon={<Clock3 className="size-3.5" />}
          label="Needs attention"
          value={isLoading ? 'Loading' : counts.warning + counts.unknown}
        />
        <StatCard
          icon={<PlayCircle className="size-3.5" />}
          label="Actions ready"
          value={isLoading ? 'Loading' : counts.actionsReady}
        />
      </StatGrid>
      <SplitView
        inspector={
          <InspectorSection label="Selected pipeline">
            {selected ? (
              <PipelineInspector
                onOpenRunAction={(action) =>
                  setRunAction({ action, pipeline: selected })
                }
                pipeline={selected}
              />
            ) : (
              <p className="text-muted-foreground text-xs">
                {isLoading
                  ? 'Reading live freshness, stage state, and action eligibility.'
                  : 'Select a Dataset pipeline to inspect freshness, affected scope, and the next supported action.'}
              </p>
            )}
          </InspectorSection>
        }
        inspectorWidth="w-[24rem]"
        list={
          <SectionCard className="ring-0" title="Recovery queue">
            <div className="text-muted-foreground grid grid-cols-[10px_minmax(0,1.2fr)_minmax(0,0.7fr)_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,0.8fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
              <span />
              <span>Dataset</span>
              <span>Freshness</span>
              <span>Stage evidence</span>
              <span>Next action</span>
              <span>Run evidence</span>
            </div>
            {isLoading ? (
              <LoadingBlock className="p-3" label="Loading pipelines" />
            ) : result.error ? (
              <EmptyBlock
                description={result.error}
                title="Pipelines unavailable"
              />
            ) : pipelines.length ? (
              <div className="divide-border divide-y">
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
              <EmptyBlock
                description="No Dataset pipelines are available yet."
                title="No Dataset pipeline available"
              />
            )}
          </SectionCard>
        }
      />
      {runAction && (
        <RunActionDialog
          action={runAction.action}
          onClose={() => setRunAction(null)}
          pipeline={runAction.pipeline}
        />
      )}
    </Page>
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
  const selectWithKeyboard = (event: ReactKeyboardEvent) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onSelect()
  }
  return (
    <div
      className={cn(
        'hover:bg-accent/50 grid w-full cursor-pointer grid-cols-[10px_minmax(0,1.2fr)_minmax(0,0.7fr)_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,0.8fr)] items-center gap-3 px-3 py-2 transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      onClick={onSelect}
      onKeyDown={selectWithKeyboard}
      role="row"
      tabIndex={0}
    >
      <HealthDot state={pipeline.freshness_state} />
      <div className="min-w-0">
        <div className="text-foreground flex items-center gap-1.5 text-xs font-medium">
          <PlayCircle className="text-muted-foreground size-3.5 flex-none" />
          <span className="truncate">
            {dataset?.name ?? 'Unassigned pipeline'}
          </span>
        </div>
        <div className="text-muted-foreground truncate font-mono text-[10px]">
          {pipeline.last_run?.label ?? fallbackPipelineDetail(pipeline)}
        </div>
      </div>
      <div className="min-w-0">
        <div className="text-foreground font-mono text-[10px] tracking-wide uppercase">
          {stateLabel(pipeline.freshness_state)}
        </div>
        <div className="text-muted-foreground truncate font-mono text-[10px]">
          {pipeline.freshness_at ?? freshnessFallback(pipeline)}
        </div>
      </div>
      <div className="flex min-w-0 items-center gap-1 overflow-hidden">
        {pipeline.stages.map((stage) => (
          <span
            className="border-border flex min-w-0 flex-col border px-1.5 py-0.5"
            key={stage.id}
            title={stageEvidenceLabel(stage)}
          >
            <span className="flex items-center gap-1">
              <HealthDot state={stage.state} />
              <span className="text-foreground truncate font-mono text-[9px]">
                {stage.label}
              </span>
            </span>
          </span>
        ))}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        {readyActions.length > 0 ? (
          readyActions.map((action) =>
            isRunActionControl(action) ? (
              <Button
                key={action.id}
                onClick={(event) => {
                  event.stopPropagation()
                  onOpenRunAction(action)
                }}
                size="xs"
                title={`${action.label} run ${action.background_operation_id ?? ''}`.trim()}
                type="button"
                variant="outline"
              >
                {action.label}
              </Button>
            ) : (
              <span
                className="text-muted-foreground font-mono text-[10px]"
                key={action.id}
              >
                {action.label}
              </span>
            ),
          )
        ) : (
          <span className="text-muted-foreground truncate font-mono text-[10px]">
            {nextActionFallback(pipeline)}
          </span>
        )}
      </div>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {pipeline.last_run?.id ?? runEvidenceFallback(pipeline)}
      </span>
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
    <div className="flex flex-col gap-4">
      <div>
        <div className="flex items-center gap-2">
          <span className="text-foreground text-xs font-semibold">
            {dataset?.name ?? 'Pipeline'}
          </span>
          <StatusBadge
            label={stateLabel(pipeline.freshness_state)}
            state={pipeline.freshness_state}
          />
        </div>
        <p className="text-muted-foreground mt-1 text-xs/relaxed">
          {pipelineSummary(pipeline)}
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2">
        <div className="border-border border px-2.5 py-2">
          <span className="text-muted-foreground block text-[10px] font-medium tracking-widest uppercase">
            Why it matters
          </span>
          <span className="text-foreground mt-0.5 block text-[11px]/relaxed">
            {recoveryImpact(pipeline)}
          </span>
        </div>
        <div className="border-border border px-2.5 py-2">
          <span className="text-muted-foreground block text-[10px] font-medium tracking-widest uppercase">
            Next action
          </span>
          <span className="text-foreground mt-0.5 block text-[11px]/relaxed">
            {primaryActionLabel(pipeline)}
          </span>
        </div>
      </div>
      <div className="divide-border divide-y border-y">
        {dataset && (
          <Link
            className="hover:bg-accent/50 flex items-center justify-between gap-2 py-1.5 transition-colors"
            params={{ datasetId: dataset.id }}
            to="/datasets/$datasetId"
          >
            <span className="text-foreground text-[11px]">Open Dataset</span>
            <span className="text-muted-foreground font-mono text-[10px]">
              {[dataset.publication_state, dataset.readiness_state]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </Link>
        )}
        {pipeline.last_run && (
          <Link
            className="hover:bg-accent/50 flex items-center justify-between gap-2 py-1.5 transition-colors"
            search={{ operationId: pipeline.last_run.id }}
            to="/operations"
          >
            <span className="text-foreground text-[11px]">
              Open recovery run
            </span>
            <span className="text-muted-foreground font-mono text-[10px]">
              {pipeline.last_run.label}
            </span>
          </Link>
        )}
        {dataset && (
          <Link
            className="hover:bg-accent/50 flex items-center justify-between gap-2 py-1.5 transition-colors"
            to="/quality"
          >
            <span className="text-foreground text-[11px]">
              Review quality evidence
            </span>
            <span className="text-muted-foreground font-mono text-[10px]">
              Checks and failure context for this Dataset
            </span>
          </Link>
        )}
      </div>
      <InspectorSection label="Stage evidence">
        <div className="divide-border divide-y border-y">
          {pipeline.stages.map((stage) => (
            <div className="flex items-start gap-2 py-1.5" key={stage.id}>
              <HealthDot className="mt-1" state={stage.state} />
              <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
                <span className="text-foreground text-[11px]">
                  {stage.label}
                </span>
                <span className="text-muted-foreground truncate font-mono text-[10px]">
                  {[
                    stateLabel(stage.state),
                    stage.resource?.label ?? stageEvidenceLabel(stage),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
            </div>
          ))}
        </div>
      </InspectorSection>
      <InspectorSection label="Action eligibility">
        <div className="divide-border divide-y border-y">
          {pipeline.actions.map((action) => (
            <div className="flex items-start gap-2 py-1.5" key={action.id}>
              <HealthDot
                className="mt-1"
                state={action.enabled ? 'ok' : 'unknown'}
              />
              <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
                <span className="text-foreground text-[11px]">
                  {isRunActionControl(action) ? (
                    <Button
                      onClick={() => onOpenRunAction(action)}
                      size="xs"
                      type="button"
                      variant="outline"
                    >
                      {action.label} run
                    </Button>
                  ) : (
                    action.label
                  )}
                </span>
                <span className="text-muted-foreground font-mono text-[10px]">
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
                </span>
              </span>
            </div>
          ))}
        </div>
      </InspectorSection>
    </div>
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
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
    >
      <button
        aria-label="Close dialog"
        className="bg-background/80 absolute inset-0 cursor-default backdrop-blur-sm"
        onClick={onClose}
        type="button"
      />
      <div className="bg-sheet border-rule relative flex max-h-[85vh] w-full max-w-lg flex-col gap-3 overflow-y-auto p-4 border">
        <div className="flex items-center justify-between gap-2">
          <span className="text-foreground text-sm font-semibold">
            {action.label} run
          </span>
          <Badge variant="outline">{action.risk_level} risk</Badge>
        </div>
        <p className="text-muted-foreground text-xs/relaxed">
          Guarded orchestration action for run{' '}
          <strong className="text-foreground">{runId}</strong> on{' '}
          {pipeline.dataset?.name ?? 'this pipeline'}. Review the guard evidence
          before confirming; the request is idempotent, so resubmitting the same
          intent can never double-invoke the provider.
        </p>
        <FactGrid>
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
        </FactGrid>
        <div className="divide-border divide-y border-y">
          <div className="flex items-center justify-between gap-2 py-1.5">
            <span className="text-foreground text-[11px]">
              Expected evidence
            </span>
            <span className="text-muted-foreground font-mono text-[10px]">
              {action.expected_evidence.join(' · ')}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2 py-1.5">
            <span className="text-foreground text-[11px]">Idempotency key</span>
            <span className="text-muted-foreground font-mono text-[10px]">
              {idempotencyKey}
            </span>
          </div>
        </div>
        {outcomeSummary && outcome?.data && (
          <div
            className={cn(
              'flex flex-col gap-1 border px-3 py-2',
              TONE_CLASS[outcomeSummary.tone],
            )}
          >
            <span className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
              Outcome
            </span>
            <strong className="text-foreground text-xs">
              {outcomeSummary.headline}
            </strong>
            <span className="text-muted-foreground font-mono text-[10px] break-all">
              {outcomeSummary.detail}
            </span>
            {outcome.data.message && (
              <span className="text-muted-foreground font-mono text-[10px] break-all">
                {outcome.data.message}
              </span>
            )}
            {canonical && (
              <Link
                className="text-primary text-[11px] underline-offset-2 hover:underline"
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
            className={cn(
              'flex flex-col gap-1 border px-3 py-2',
              TONE_CLASS[
                verification
                  ? VERIFICATION_TONES[verification.state]
                  : 'warning'
              ],
            )}
          >
            <span className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
              Verification
            </span>
            {verification ? (
              <>
                <strong className="text-foreground text-xs">
                  {verification.headline}
                </strong>
                <span className="text-muted-foreground font-mono text-[10px] break-all">
                  {verification.detail}
                </span>
                {verification.identity && (
                  <Link
                    className="text-primary text-[11px] underline-offset-2 hover:underline"
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
              <strong className="text-foreground text-xs">
                Checking durable run evidence…
              </strong>
            )}
            {verifying && (
              <Button
                onClick={stopVerification}
                size="xs"
                type="button"
                variant="outline"
              >
                Stop verifying
              </Button>
            )}
            {verificationStopped && (
              <span className="text-muted-foreground font-mono text-[10px]">
                Verification stopped before complete evidence arrived. The
                action outcome above remains the record; nothing is claimed as
                proven.
              </span>
            )}
          </div>
        )}
        {outcome?.error && (
          <div className="border-status-error/40 bg-status-error/5 flex flex-col gap-1 border px-3 py-2">
            <strong className="text-status-error text-xs">
              Action could not be completed
            </strong>
            <span className="text-muted-foreground font-mono text-[10px] break-all">
              {outcome.error}
            </span>
          </div>
        )}
        <div className="flex items-center justify-end gap-2">
          <Button
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
          </Button>
          <Button onClick={onClose} type="button" variant="outline">
            Close
          </Button>
        </div>
      </div>
    </div>
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
