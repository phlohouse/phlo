/**
 * /runs route. Run list sorted by status and recency; when the API returns
 * no native runs it falls back to runs recovered from operation records.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Clock3,
  ListChecks,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryMetadata,
  ObservatoryOperation,
  ObservatoryRun,
  ObservatoryRunReportIdentity,
} from '@/observatory/api/types'
import {
  getObservatoryOperationRecords,
  getObservatoryRunRecords,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { ObservatoryIndexTable } from '@/observatory/components/ObservatoryTable'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/runs')({
  component: Runs,
})

export function Runs() {
  const result = useLiveResource(
    getObservatoryRunRecords,
    60_000,
    'observatory:runs',
  )
  const operationResult = useLiveResource(
    getObservatoryOperationRecords,
    60_000,
    'observatory:operations',
  )
  const fallbackRuns = useMemo(
    () => operationsAsRecoveredRuns(operationResult.data ?? []),
    [operationResult.data],
  )
  const nativeRuns = result.data ?? []
  const usingRecoveredRuns = nativeRuns.length === 0 && fallbackRuns.length > 0
  const runs = useMemo(
    () =>
      [...(usingRecoveredRuns ? fallbackRuns : nativeRuns)].sort(compareRuns),
    [fallbackRuns, nativeRuns, usingRecoveredRuns],
  )
  const isLoading =
    runs.length === 0 && (result.isLoading || operationResult.isLoading)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected =
    runs.find((run) => run.id === selectedId) ??
    runs.find((run) => run.status === 'failed') ??
    runs[0] ??
    null
  const counts = useMemo(() => countRuns(runs), [runs])
  const selectRun = useCallback((runId: string) => {
    setSelectedId(runId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('runId', runId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get('runId')
    if (!requested || requested === selectedId) return
    if (runs.some((run) => run.id === requested)) {
      setSelectedId(requested)
    }
  }, [runs, selectedId])

  useEffect(() => {
    if (selectedId !== null || !selected) return
    setSelectedId(selected.id)
  }, [selected, selectedId])

  return (
    <ObservatoryPage
      kicker="Operations"
      title="Runs"
      description={
        usingRecoveredRuns
          ? 'Recovered run evidence from live operations while dedicated run history is unavailable.'
          : 'Run history, affected scope, and handoff to recovery evidence.'
      }
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${runs.length} runs`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-runs-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-run-list-surface">
          <div className="phlo-observatory-command-strip phlo-observatory-run-summary">
            <Metric
              icon={<CheckCircle2 className="size-4" />}
              label="Succeeded"
              value={counts.succeeded}
            />
            <Metric
              icon={<AlertCircle className="size-4" />}
              label="Failed"
              value={counts.failed}
            />
            <Metric
              icon={<Clock3 className="size-4" />}
              label="Running"
              value={counts.running}
            />
            <Metric
              icon={<ListChecks className="size-4" />}
              label="Visible"
              value={isLoading ? 'Loading' : runs.length}
            />
          </div>

          {isLoading ? (
            <RunProviderEmpty loading />
          ) : runs.length > 0 ? (
            <>
              {usingRecoveredRuns && (
                <div className="phlo-observatory-panel-note">
                  Dedicated run history has no rows; showing recovered operation
                  runs with the same recovery evidence.
                </div>
              )}
              <ObservatoryIndexTable
                columnTemplate="10px minmax(220px, 1.25fr) minmax(86px, 0.45fr) minmax(176px, 0.75fr) minmax(86px, 0.35fr) minmax(190px, 0.8fr)"
                columns={[
                  { key: 'state', label: '' },
                  { key: 'run', label: 'Run' },
                  { key: 'status', label: 'Status' },
                  { key: 'started', label: 'Started' },
                  { key: 'duration', label: 'Duration' },
                  { key: 'evidence', label: 'Evidence' },
                ]}
                rows={runs.map((run) => ({
                  active: run.id === selected?.id,
                  key: run.id,
                  onSelect: () => selectRun(run.id),
                  status: run.status,
                  cells: [
                    <span
                      className="phlo-observatory-dot"
                      data-state={stateForStatus(run.status)}
                    />,
                    <RunSummary run={run} />,
                    <span className="phlo-observatory-pill">{run.status}</span>,
                    run.started_at ?? 'not timestamped',
                    formatDuration(run.duration_seconds),
                    `${run.assets.length} affected Datasets · ${run.checks.length} checks · ${run.logs.length} logs`,
                  ],
                }))}
              />
            </>
          ) : (
            <RunProviderEmpty error={result.error ?? operationResult.error} />
          )}
        </div>

        <aside className="phlo-observatory-inspector">
          <div className="phlo-observatory-inspector-label">Run evidence</div>
          {selected ? (
            <SelectedRun run={selected} />
          ) : (
            <RunProviderInspector
              error={result.error ?? operationResult.error}
              loading={isLoading}
            />
          )}
          {(result.error || operationResult.error) && (
            <div className="phlo-observatory-panel-footer">
              {result.error ?? operationResult.error}
            </div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function Metric({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: string | number
}) {
  return (
    <div className="phlo-observatory-command-metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function RunSummary({ run }: { run: ObservatoryRun }) {
  return (
    <div className="phlo-observatory-run-row-main">
      <div className="phlo-observatory-row-title">
        <ListChecks className="size-4" />
        {run.name}
      </div>
      <div className="phlo-observatory-row-meta">{run.id}</div>
    </div>
  )
}

function SelectedRun({ run }: { run: ObservatoryRun }) {
  return (
    <>
      <h2>{run.name}</h2>
      <p>{runNarrative(run)}</p>
      <dl className="phlo-observatory-facts">
        <Fact label="Status" value={run.status} />
        <Fact label="Started" value={run.started_at ?? 'not reported'} />
        <Fact label="Completed" value={run.completed_at ?? 'not completed'} />
        <Fact label="Duration" value={formatDuration(run.duration_seconds)} />
        <Fact label="Affected Datasets" value={run.assets.length} />
        <Fact label="Checks" value={run.checks.length} />
        <Fact label="Logs" value={run.logs.length} />
      </dl>
      {runFailureReason(run) && (
        <div className="phlo-observatory-detail-list">
          <div className="phlo-observatory-mini-row phlo-observatory-run-evidence-row">
            <span>Failure reason</span>
            <small>{runFailureReason(run)}</small>
          </div>
        </div>
      )}
      <RelatedList title="Affected Datasets" refs={run.assets} />
      <RelatedList title="Checks" refs={run.checks} />
      <RelatedList title="Logs" refs={run.logs} />
      {typeof run.metadata.operation_id === 'string' && (
        <div className="phlo-observatory-detail-list">
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            to="/operations"
            search={{ operationId: run.metadata.operation_id }}
          >
            <span>Open operation evidence</span>
            <small>{run.metadata.operation_id}</small>
          </Link>
        </div>
      )}
      <RunReportLink run={run} />
    </>
  )
}

export function RunReportLink({ run }: { run: ObservatoryRun }) {
  const identity = runReportIdentity(run)
  if (!identity) return null
  return (
    <div className="phlo-observatory-detail-list">
      <Link
        className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
        to="/runs/$projectId/$runId/attempts/$attempt/report"
        params={{
          projectId: identity.project_id,
          runId: identity.run_id,
          attempt: String(identity.attempt),
        }}
      >
        <span>Open run report</span>
        <small>
          {identity.project_id}/{identity.run_id} · attempt {identity.attempt}
        </small>
      </Link>
    </div>
  )
}

export function runReportIdentity(
  run: ObservatoryRun,
): ObservatoryRunReportIdentity | null {
  const identity = run.report_identity
  if (!identity) return null
  const { project_id, run_id, attempt } = identity
  if (
    typeof project_id !== 'string' ||
    !project_id.trim() ||
    typeof run_id !== 'string' ||
    !run_id.trim()
  ) {
    return null
  }
  if (
    typeof attempt !== 'number' ||
    !Number.isSafeInteger(attempt) ||
    attempt < 1
  ) {
    return null
  }
  return { project_id, run_id, attempt }
}

function RunProviderEmpty({
  error = null,
  loading = false,
}: {
  error?: string | null
  loading?: boolean
}) {
  if (loading) {
    return (
      <div className="phlo-observatory-run-provider-empty">
        <div>
          <span className="phlo-observatory-inspector-label">Run history</span>
          <h2>Loading run history</h2>
          <p>Reading live run evidence and recovery context.</p>
        </div>
      </div>
    )
  }
  return (
    <div className="phlo-observatory-run-provider-empty">
      <div>
        <span className="phlo-observatory-inspector-label">Run history</span>
        <h2>
          {error ? 'Run history unavailable' : 'No dedicated runs available'}
        </h2>
        <p>
          Operations and Pipelines still show what is wrong, the affected scope,
          and the next supported recovery step. Use this page again once
          dedicated run rows are available.
        </p>
      </div>
      <div className="phlo-observatory-run-provider-actions">
        <Link to="/operations">
          <Activity className="size-4" />
          Open Operations
        </Link>
        <Link to="/pipelines">
          <ListChecks className="size-4" />
          Open Pipelines
        </Link>
      </div>
      <dl>
        <dt>Current evidence</dt>
        <dd>Operations and Pipeline recovery rows</dd>
        <dt>Rows</dt>
        <dd>No dedicated run rows available</dd>
        <dt>Next action</dt>
        <dd>Open Operations for failures or Pipelines for stale Datasets</dd>
      </dl>
    </div>
  )
}

function RunProviderInspector({
  error,
  loading = false,
}: {
  error: string | null
  loading?: boolean
}) {
  if (loading) {
    return (
      <>
        <h2>Loading run evidence</h2>
        <p>Reading live run evidence and recovery context.</p>
      </>
    )
  }
  return (
    <>
      <h2>
        {error ? 'Run history unavailable' : 'No dedicated runs available'}
      </h2>
      <p>
        Run rows are not available yet. Recovery operations and Dataset
        pipelines still show failures, affected scope, linked evidence, and the
        next supported action.
      </p>
      <div className="phlo-observatory-detail-list">
        <Link
          className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
          to="/operations"
        >
          <span>Open Operations</span>
          <small>Failed, running, and completed recovery records</small>
        </Link>
        <Link
          className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
          to="/pipelines"
        >
          <span>Open Pipelines</span>
          <small>Freshness, stage state, and action eligibility</small>
        </Link>
      </div>
    </>
  )
}

function RelatedList({
  title,
  refs,
}: {
  title: string
  refs: ObservatoryRun['assets']
}) {
  return (
    <div className="phlo-observatory-detail-list">
      <div className="phlo-observatory-mini-row">
        <span>{title}</span>
        <small>
          {refs.length > 0 ? refs.map((ref) => ref.label).join(', ') : 'none'}
        </small>
      </div>
    </div>
  )
}

function formatDuration(duration: number | null | undefined): string {
  if (duration === null || duration === undefined) return 'not reported'
  return `${duration}s`
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
      <dd>{value === null ? 'not reported' : String(value)}</dd>
    </>
  )
}

function countRuns(
  runs: Array<ObservatoryRun>,
): Record<ObservatoryRun['status'], number> {
  return runs.reduce(
    (counts, run) => {
      counts[run.status] += 1
      return counts
    },
    {
      queued: 0,
      running: 0,
      succeeded: 0,
      failed: 0,
      cancelled: 0,
      unknown: 0,
    },
  )
}

function stateForStatus(status: ObservatoryRun['status']) {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed' || status === 'cancelled') return 'error'
  if (status === 'running' || status === 'queued') return 'warning'
  return 'unknown'
}

function compareRuns(left: ObservatoryRun, right: ObservatoryRun): number {
  return runScore(right) - runScore(left)
}

function runScore(run: ObservatoryRun): number {
  const time = Date.parse(run.completed_at ?? run.started_at ?? '')
  let score = Number.isNaN(time) ? 0 : time / 1_000_000
  if (run.status === 'failed') score += 1_000_000
  if (run.status === 'running') score += 500_000
  return score
}

function runNarrative(run: ObservatoryRun): string {
  const resourceCount = `${run.assets.length} Dataset link${run.assets.length === 1 ? '' : 's'}`
  const checkCount = `${run.checks.length} check${run.checks.length === 1 ? '' : 's'}`
  if (run.status === 'failed') {
    return `Failed run with ${resourceCount}, ${checkCount}, and ${run.logs.length} linked logs.`
  }
  if (run.status === 'succeeded') {
    return `Succeeded run with ${resourceCount} and ${checkCount}.`
  }
  return `${run.status} run with ${resourceCount} and ${checkCount}.`
}

function runFailureReason(run: ObservatoryRun): string | null {
  const reason = run.metadata.failure_reason ?? run.metadata.error
  return typeof reason === 'string' && reason ? reason : null
}

function operationsAsRecoveredRuns(
  operations: Array<ObservatoryOperation>,
): Array<ObservatoryRun> {
  return operations.map((operation) => {
    const target = operation.target ? [operation.target] : []
    const metadata: ObservatoryMetadata = {
      ...operation.metadata,
      operation_id: operation.id,
      recovered_from: 'operation',
    }
    if (operation.status === 'failed' && operation.health.message) {
      metadata.failure_reason = operation.health.message
    }
    return {
      id: operation.id,
      name: operation.name,
      status: runStatusFromOperation(operation.status),
      started_at: operation.started_at,
      completed_at: operation.completed_at,
      duration_seconds: operation.duration_seconds,
      assets: target,
      checks: [],
      logs: [],
      metadata,
    }
  })
}

function runStatusFromOperation(
  status: ObservatoryOperation['status'],
): ObservatoryRun['status'] {
  if (status === 'queued') return 'queued'
  if (status === 'running') return 'running'
  if (status === 'succeeded') return 'succeeded'
  if (status === 'failed') return 'failed'
  return 'unknown'
}
