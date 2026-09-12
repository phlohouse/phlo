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
import { ObservatoryIndexTable } from '@/observatory/components/ObservatoryTable'
import { useLiveResource } from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { HealthDot, StatusBadge } from '@/components/observatory/status'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

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
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading ? 'Loading' : `${runs.length} runs`}
          </Badge>
        }
        description={
          usingRecoveredRuns
            ? 'Recovered run evidence from live operations while dedicated run history is unavailable.'
            : 'Run history, affected scope, and handoff to recovery evidence.'
        }
        title="Runs"
      />
      <StatGrid>
        <StatCard
          icon={<CheckCircle2 className="size-3.5" />}
          label="Succeeded"
          value={counts.succeeded}
        />
        <StatCard
          icon={<AlertCircle className="size-3.5" />}
          label="Failed"
          state={counts.failed > 0 ? 'error' : 'ok'}
          value={counts.failed}
        />
        <StatCard
          icon={<Clock3 className="size-3.5" />}
          label="Running"
          value={counts.running}
        />
        <StatCard
          icon={<ListChecks className="size-3.5" />}
          label="Visible"
          value={isLoading ? 'Loading' : runs.length}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection label="Run evidence">
              {selected ? (
                <SelectedRun run={selected} />
              ) : (
                <RunProviderInspector
                  error={result.error ?? operationResult.error}
                  loading={isLoading}
                />
              )}
            </InspectorSection>
            {(result.error ?? operationResult.error) && (
              <p className="text-status-error font-mono text-[10px] break-all">
                {result.error ?? operationResult.error}
              </p>
            )}
          </>
        }
        inspectorWidth="w-[24rem]"
        list={
          <div className="flex min-h-0 flex-1 flex-col">
            {usingRecoveredRuns && (
              <p className="text-muted-foreground border-b px-3 py-2 text-[11px]">
                Dedicated run history has no rows; showing recovered operation
                runs with the same recovery evidence.
              </p>
            )}
            {isLoading ? (
              <LoadingBlock className="p-3" label="Loading run history" />
            ) : runs.length > 0 ? (
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
                    <HealthDot state={stateForStatus(run.status)} />,
                    <RunSummary run={run} />,
                    <span className="font-mono text-[10px] tracking-wide uppercase">
                      {run.status}
                    </span>,
                    <span className="text-muted-foreground font-mono text-[10px]">
                      {run.started_at ?? 'not timestamped'}
                    </span>,
                    <span className="text-muted-foreground font-mono text-[10px]">
                      {formatDuration(run.duration_seconds)}
                    </span>,
                    <span className="text-muted-foreground font-mono text-[10px]">
                      {`${run.assets.length} affected Datasets · ${run.checks.length} checks · ${run.logs.length} logs`}
                    </span>,
                  ],
                }))}
              />
            ) : (
              <RunProviderEmpty error={result.error ?? operationResult.error} />
            )}
          </div>
        }
      />
    </Page>
  )
}

function RunSummary({ run }: { run: ObservatoryRun }) {
  return (
    <div className="min-w-0">
      <div className="text-foreground flex items-center gap-1.5 text-xs font-medium">
        <ListChecks className="text-muted-foreground size-3.5 flex-none" />
        <span className="truncate">{run.name}</span>
      </div>
      <div className="text-muted-foreground truncate font-mono text-[10px]">
        {run.id}
      </div>
    </div>
  )
}

function SelectedRun({ run }: { run: ObservatoryRun }) {
  return (
    <>
      <div className="flex items-center gap-2">
        <span className="text-foreground text-xs font-semibold">
          {run.name}
        </span>
        <StatusBadge state={stateForStatus(run.status)} label={run.status} />
      </div>
      <p className="text-muted-foreground text-xs/relaxed">
        {runNarrative(run)}
      </p>
      <FactGrid>
        <Fact label="Status" value={run.status} />
        <Fact label="Started" value={run.started_at ?? 'not reported'} />
        <Fact label="Completed" value={run.completed_at ?? 'not completed'} />
        <Fact label="Duration" value={formatDuration(run.duration_seconds)} />
        <Fact label="Affected Datasets" value={run.assets.length} />
        <Fact label="Checks" value={run.checks.length} />
        <Fact label="Logs" value={run.logs.length} />
      </FactGrid>
      {runFailureReason(run) && (
        <div className="border-status-error/40 bg-status-error/5 px-2.5 py-2">
          <span className="text-status-error block text-[10px] font-medium tracking-widest uppercase">
            Failure reason
          </span>
          <span className="text-foreground font-mono text-[11px] break-all">
            {runFailureReason(run)}
          </span>
        </div>
      )}
      <RelatedList title="Affected Datasets" refs={run.assets} />
      <RelatedList title="Checks" refs={run.checks} />
      <RelatedList title="Logs" refs={run.logs} />
      {typeof run.metadata.operation_id === 'string' && (
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 border-y py-1.5 transition-colors"
          search={{ operationId: run.metadata.operation_id }}
          to="/operations"
        >
          <span className="text-foreground text-[11px]">
            Open operation evidence
          </span>
          <span className="text-muted-foreground font-mono text-[10px]">
            {run.metadata.operation_id}
          </span>
        </Link>
      )}
      <RunReportLink run={run} />
    </>
  )
}

export function RunReportLink({ run }: { run: ObservatoryRun }) {
  const identity = runReportIdentity(run)
  if (!identity) return null
  return (
    <Link
      className="hover:bg-accent/50 flex items-center justify-between gap-2 border-y py-1.5 transition-colors"
      params={{
        projectId: identity.project_id,
        runId: identity.run_id,
        attempt: String(identity.attempt),
      }}
      to="/runs/$projectId/$runId/attempts/$attempt/report"
    >
      <span className="text-foreground text-[11px]">Open run report</span>
      <span className="text-muted-foreground font-mono text-[10px]">
        {identity.project_id}/{identity.run_id} · attempt {identity.attempt}
      </span>
    </Link>
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
    return <LoadingBlock className="p-3" label="Loading run history" />
  }
  return (
    <div className="flex flex-col gap-4 p-6">
      <EmptyBlock
        description="Operations and Pipelines still show what is wrong, the affected scope, and the next supported recovery step. Use this page again once dedicated run rows are available."
        title={
          error ? 'Run history unavailable' : 'No dedicated runs available'
        }
      />
      <div className="flex items-center justify-center gap-2">
        <Link
          className={cn(buttonVariants({ size: 'sm', variant: 'outline' }))}
          to="/operations"
        >
          <Activity className="size-3.5" />
          Open Operations
        </Link>
        <Link
          className={cn(buttonVariants({ size: 'sm', variant: 'outline' }))}
          to="/pipelines"
        >
          <ListChecks className="size-3.5" />
          Open Pipelines
        </Link>
      </div>
      <FactGrid className="mx-auto max-w-md">
        <Fact
          label="Current evidence"
          value="Operations and Pipeline recovery rows"
        />
        <Fact label="Rows" value="No dedicated run rows available" />
        <Fact
          label="Next action"
          value="Open Operations for failures or Pipelines for stale Datasets"
        />
      </FactGrid>
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
      <p className="text-muted-foreground text-xs">
        Reading live run evidence and recovery context.
      </p>
    )
  }
  return (
    <>
      <p className="text-foreground text-xs font-semibold">
        {error ? 'Run history unavailable' : 'No dedicated runs available'}
      </p>
      <p className="text-muted-foreground text-xs/relaxed">
        Run rows are not available yet. Recovery operations and Dataset
        pipelines still show failures, affected scope, linked evidence, and the
        next supported action.
      </p>
      <div className="divide-border divide-y border-y">
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 py-1.5 transition-colors"
          to="/operations"
        >
          <span className="text-foreground text-[11px]">Open Operations</span>
          <span className="text-muted-foreground font-mono text-[10px]">
            Failed, running, and completed recovery records
          </span>
        </Link>
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 py-1.5 transition-colors"
          to="/pipelines"
        >
          <span className="text-foreground text-[11px]">Open Pipelines</span>
          <span className="text-muted-foreground font-mono text-[10px]">
            Freshness, stage state, and action eligibility
          </span>
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
    <div className="flex items-center justify-between gap-2 border-b py-1.5">
      <span className="text-foreground text-[11px]">{title}</span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {refs.length > 0 ? refs.map((ref) => ref.label).join(', ') : 'none'}
      </span>
    </div>
  )
}

function formatDuration(duration: number | null | undefined): string {
  if (duration === null || duration === undefined) return 'not reported'
  return `${duration}s`
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
