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
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryDatasetPipeline,
  ObservatoryPipelineStage,
} from '@/observatory/api/types'
import { getObservatoryPipelineRecords } from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/pipelines')({
  component: Pipelines,
})

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
            <PipelineInspector pipeline={selected} />
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
    </ObservatoryPage>
  )
}

function PipelineRow({
  onSelect,
  pipeline,
  selected,
}: {
  onSelect: () => void
  pipeline: ObservatoryDatasetPipeline
  selected: boolean
}) {
  const dataset = pipeline.dataset
  const readyActions = pipeline.actions.filter((action) => action.enabled)
  return (
    <button
      className="phlo-observatory-pipeline-row"
      data-active={selected}
      data-state={pipeline.freshness_state}
      onClick={onSelect}
      role="row"
      type="button"
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
          ? readyActions.map((action) => (
              <span
                className="phlo-observatory-pipeline-action"
                data-enabled={action.enabled}
                key={action.id}
              >
                {action.label}
              </span>
            ))
          : nextActionFallback(pipeline)}
      </div>
      <span>{pipeline.last_run?.id ?? runEvidenceFallback(pipeline)}</span>
    </button>
  )
}

function PipelineInspector({
  pipeline,
}: {
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
            <span>{action.label}</span>
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
