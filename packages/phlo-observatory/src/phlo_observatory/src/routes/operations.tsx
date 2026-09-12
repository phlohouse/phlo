/**
 * /operations route. Operation records with detail drill-down, a dependency
 * graph on the flow canvas, operation actions, and linked quality checks.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  CheckCircle2,
  Clock3,
  Database,
  FileText,
  RotateCcw,
  ShieldAlert,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryOperation,
  ObservatoryOperationDetail,
  ObservatoryQualityCheck,
  ObservatoryResourceRef,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import type {
  ObservatoryFlowEdge,
  ObservatoryFlowNode,
} from '@/observatory/components/ObservatoryFlowCanvas'
import {
  getObservatoryOperationDetail,
  getObservatoryOperationDetailDirect,
  getObservatoryOperationRecords,
  getObservatoryOperationRecordsDirect,
  getObservatoryQualityRecords,
  runObservatoryAction,
} from '@/observatory/api/resources'
import { ActionButton } from '@/observatory/components/ActionButton'
import { ObservatoryFlowCanvas } from '@/observatory/components/ObservatoryFlowCanvas'
import {
  invalidateCachedResources,
  readMetric,
  useLiveResource,
} from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/operations')({
  component: Operations,
})

export function Operations() {
  const result = useLiveResource(
    getObservatoryOperationRecords,
    120_000,
    'observatory:operations',
  )
  const qualityResult = useLiveResource(
    getObservatoryQualityRecords,
    120_000,
    'observatory:quality',
  )
  const operations = result.data ?? []
  const [directResult, setDirectResult] = useState<ObservatoryResourceResult<
    Array<ObservatoryOperation>
  > | null>(null)
  const directOperations = directResult?.data ?? []
  const isLoading =
    result.isLoading ||
    (operations.length === 0 && directResult === null && !result.error)
  const qualityChecks = qualityResult.data ?? []
  const [localOperations, setLocalOperations] = useState<
    Array<ObservatoryOperation>
  >([])
  const apiOperations =
    directOperations.length > 0 || operations.length === 0
      ? directOperations
      : operations
  const visibleOperations = mergeOperations(localOperations, apiOperations)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const defaultOperation = chooseDefaultOperation(visibleOperations)
  const latest =
    visibleOperations.find((operation) => operation.id === selectedId) ??
    defaultOperation ??
    null
  const displayedOperations = visibleOperations.slice(0, 100)
  const hiddenOperationCount = Math.max(
    0,
    visibleOperations.length - displayedOperations.length,
  )
  const [detail, setDetail] = useState<
    ObservatoryResourceResult<ObservatoryOperationDetail>
  >({
    data: null,
    error: null,
  })
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const failed = visibleOperations.filter(
    (operation) => operation.status === 'failed',
  ).length
  const recovered = visibleOperations.filter(
    (operation) => operation.status === 'succeeded',
  ).length
  const ledger = useMemo(
    () => buildOperationLedger(visibleOperations),
    [visibleOperations],
  )
  const selectedFailure = latest ? operationFailure(latest) : null
  const selectedMetadata = latest ? operationMetadata(latest) : []
  const selectedQuality = latest
    ? qualityChecks.filter(
        (check) =>
          check.asset_id === latest.target?.id && check.status !== 'passing',
      )
    : []
  const selectedIsWap = latest?.kind === 'wap'
  const selectOperation = useCallback((operationId: string) => {
    setSelectedId(operationId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('operationId', operationId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])

  useEffect(() => {
    let cancelled = false
    const refresh = () => {
      void getObservatoryOperationRecordsDirect().then((next) => {
        if (!cancelled) setDirectResult(next)
      })
    }
    refresh()
    const interval = window.setInterval(refresh, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'operationId',
    )
    if (!requested || requested === selectedId) return
    if (visibleOperations.some((operation) => operation.id === requested)) {
      setSelectedId(requested)
    }
  }, [selectedId, visibleOperations])

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (selectedId !== null || visibleOperations.length === 0) return
    const requested = new URLSearchParams(window.location.search).get(
      'operationId',
    )
    const initial =
      visibleOperations.find((operation) => operation.id === requested) ??
      chooseDefaultOperation(visibleOperations)
    if (!initial) return
    selectOperation(initial.id)
  }, [selectOperation, selectedId, visibleOperations])

  useEffect(() => {
    if (!latest) {
      setDetail({ data: null, error: null })
      return
    }
    let cancelled = false
    const loadDetail =
      typeof window === 'undefined'
        ? getObservatoryOperationDetail({ data: { operationId: latest.id } })
        : getObservatoryOperationDetailDirect({ operationId: latest.id })

    void loadDetail.then((next) => {
      if (!cancelled) setDetail(next)
    })
    return () => {
      cancelled = true
    }
  }, [latest])

  const listContent = isLoading ? (
    <SectionCard>
      <LoadingBlock
        className="p-3"
        label="Reading live recovery, service, and maintenance operation evidence"
      />
    </SectionCard>
  ) : visibleOperations.length > 0 ? (
    <>
      {!selectedIsWap && (
        <StatGrid className="xl:grid-cols-3">
          <StatCard
            icon={<CheckCircle2 className="size-3.5" />}
            label="Recovered"
            value={isLoading ? '—' : recovered}
          />
          <StatCard
            icon={<ShieldAlert className="size-3.5" />}
            label="Failed"
            state={failed ? 'error' : 'ok'}
            value={isLoading ? '—' : failed}
          />
          <StatCard
            icon={<Clock3 className="size-3.5" />}
            label="Last duration"
            value={
              latest?.duration_seconds
                ? `${latest.duration_seconds}s`
                : 'not reported'
            }
          />
        </StatGrid>
      )}
      {latest && selectedIsWap ? (
        <WapOperationFocus operation={latest} />
      ) : latest ? (
        <SectionCard
          actions={<Badge variant="secondary">{latest.status}</Badge>}
          title="Selected operation evidence"
        >
          <div className="flex flex-wrap items-start justify-between gap-3 border-b px-3 py-3">
            <div className="min-w-0">
              <span className="text-muted-foreground font-mono text-[10px] uppercase">
                {humanizeLabel(latest.kind)}
              </span>
              <h2 className="text-foreground mt-0.5 text-base font-semibold">
                {latest.name}
              </h2>
              <p className="text-muted-foreground mt-0.5 text-xs/relaxed">
                {latest.target?.label ?? 'Platform operation'}
              </p>
              {selectedFailure && (
                <div className="border-status-error/40 bg-status-error/5 mt-2 flex flex-col gap-0.5 border px-3 py-2">
                  <strong className="text-status-error text-xs">
                    {selectedFailure.title}
                  </strong>
                  <span className="text-muted-foreground font-mono text-[10px] break-all">
                    {selectedFailure.message}
                  </span>
                </div>
              )}
            </div>
            <FactGrid className="min-w-64 flex-1">
              {operationEvidenceFacts(latest).map((fact) => (
                <Fact key={fact.label} label={fact.label} value={fact.value} />
              ))}
            </FactGrid>
          </div>
          {selectedMetadata.length > 0 && (
            <div className="border-border flex flex-wrap gap-x-4 gap-y-1 border-b px-3 py-2">
              {selectedMetadata.map(([key, value]) => (
                <span className="font-mono text-[10px]" key={key}>
                  <strong className="text-muted-foreground mr-1 uppercase">
                    {humanizeKey(key)}
                  </strong>
                  <span className="text-foreground">{String(value)}</span>
                </span>
              ))}
            </div>
          )}
          <InvestigationPath detail={detail.data} operation={latest} />
        </SectionCard>
      ) : null}
      {!selectedIsWap && (
        <SectionCard
          actions={<Badge variant="secondary">{ledger.length} targets</Badge>}
          title="Target ledger"
        >
          <div className="grid grid-cols-4 max-xl:grid-cols-2">
            {ledger.map((item) => (
              <button
                className="border-border hover:bg-accent/50 flex flex-col gap-0.5 border-r border-b px-3 py-2 text-left transition-colors"
                data-state={item.state}
                key={item.id}
                onClick={() => selectOperation(item.latest.id)}
                type="button"
              >
                <span className="text-muted-foreground flex items-center gap-1.5 text-[9px] font-medium tracking-widest uppercase">
                  <span className="status-dot" data-state={item.state} />
                  {humanizeLabel(item.kind)}
                </span>
                <strong className="text-foreground truncate text-[11px]">
                  {item.label}
                </strong>
                <span className="text-muted-foreground truncate font-mono text-[10px]">
                  {operationLedgerSummary(item)}
                </span>
              </button>
            ))}
          </div>
        </SectionCard>
      )}
      {!selectedIsWap && (
        <SectionCard
          actions={
            <Badge variant="secondary">
              showing {displayedOperations.length}
              {hiddenOperationCount > 0
                ? ` of ${visibleOperations.length}`
                : ''}
            </Badge>
          }
          title="Activity stream"
        >
          <ScrollArea className="max-h-[26rem]">
            <div className="divide-border divide-y">
              {displayedOperations.map((operation) => (
                <OperationLine
                  key={operation.id}
                  onSelect={selectOperation}
                  operation={operation}
                  selected={operation.id === latest?.id}
                />
              ))}
              {hiddenOperationCount > 0 && (
                <p className="text-muted-foreground px-3 py-2 font-mono text-[10px]">
                  {hiddenOperationCount} older operations kept out of the DOM.
                  Use target selection to narrow the working set.
                </p>
              )}
            </div>
          </ScrollArea>
        </SectionCard>
      )}
    </>
  ) : (
    <SectionCard>
      <EmptyBlock
        description="Dagster owns orchestration and materialization runs. Observatory will show Phlo recovery, branch, service, and maintenance operations here once phlo-api records them."
        title="Operational history is quiet."
      />
      <div className="divide-border -mt-4 divide-y border-t">
        <InspectorlessRow detail="Managed in Dagster" label="Dagster runs" />
        <InspectorlessRow detail="0 recorded" label="Phlo operations" />
        <InspectorlessRow
          detail="No guarded action history yet"
          label="Recovery actions"
        />
      </div>
    </SectionCard>
  )

  const inspector = (
    <>
      <InspectorSection
        label={`Selected operation · ${latest?.name ?? 'none'}`}
      >
        {latest ? (
          <>
            <p className="text-muted-foreground text-xs/relaxed">
              {latest.target?.label ?? humanizeLabel(latest.kind)}
            </p>
            <FactGrid>
              {operationInspectorFacts(latest).map((fact) => (
                <Fact key={fact.label} label={fact.label} value={fact.value} />
              ))}
            </FactGrid>
          </>
        ) : (
          <p className="text-muted-foreground text-xs">
            {isLoading
              ? 'Reading live operation records and recovery evidence.'
              : 'There are no operation records for this environment yet.'}
          </p>
        )}
      </InspectorSection>
      {latest && (
        <InspectorSection label="Recovery">
          <OperationRecoveryPanel
            detail={detail.data}
            failure={selectedFailure}
            operation={latest}
            quality={selectedQuality}
          />
          {(detail.data?.actions ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-2">
              {(detail.data?.actions ?? []).map((action) => (
                <ActionButton
                  action={action}
                  key={action.id}
                  onRun={(actionId) => {
                    void runObservatoryAction({ data: { actionId } }).then(
                      (next) => {
                        const operation = next.data?.operation
                        if (operation) {
                          setLocalOperations((current) =>
                            mergeOperations([operation], current),
                          )
                          selectOperation(operation.id)
                        }
                        invalidateCachedResources(['observatory:operations'])
                        setActionMessage(
                          next.data?.message ??
                            next.error ??
                            'Action requested',
                        )
                      },
                    )
                  }}
                />
              ))}
            </div>
          )}
          {actionMessage && (
            <p className="text-muted-foreground pt-2 font-mono text-[10px] break-all">
              {actionMessage}
            </p>
          )}
          {selectedFailure && (
            <div className="border-status-error/40 bg-status-error/5 mt-2 flex flex-col gap-0.5 border px-3 py-2">
              <strong className="text-status-error text-xs">
                {selectedFailure.title}
              </strong>
              <span className="text-muted-foreground font-mono text-[10px] break-all">
                {selectedFailure.message}
              </span>
            </div>
          )}
        </InspectorSection>
      )}
      {(detail.error ?? result.error ?? directResult?.error) && (
        <InspectorSection label="Errors">
          {detail.error && (
            <p className="text-status-error font-mono text-[10px] break-all">
              {detail.error}
            </p>
          )}
          {result.error && (
            <p className="text-status-error font-mono text-[10px] break-all">
              {result.error}
            </p>
          )}
          {!result.error && directResult?.error && (
            <p className="text-status-error font-mono text-[10px] break-all">
              {directResult.error}
            </p>
          )}
        </InspectorSection>
      )}
    </>
  )

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading ? 'Loading' : `${visibleOperations.length} operations`}
          </Badge>
        }
        description={
          selectedIsWap && latest
            ? 'Branch publish evidence, affected tables, and target hash movement.'
            : latest
              ? operationPageDescription(latest)
              : 'Recovery operations, affected scope, evidence, and supported next steps.'
        }
        title={latest ? latest.name : 'Recovery activity'}
      />
      {selectedIsWap ? (
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          {listContent}
        </div>
      ) : (
        <SplitView
          inspector={inspector}
          inspectorWidth="w-[24rem]"
          list={
            <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
              {listContent}
            </div>
          }
        />
      )}
    </Page>
  )
}

function InspectorlessRow({
  detail,
  label,
}: {
  detail: string
  label: string
}) {
  return (
    <div className="flex items-center justify-between gap-2 px-3 py-2">
      <span className="text-foreground text-[11px]">{label}</span>
      <span className="text-muted-foreground font-mono text-[10px]">
        {detail}
      </span>
    </div>
  )
}

function InvestigationPath({
  detail,
  operation,
}: {
  detail: ObservatoryOperationDetail | null
  operation: ObservatoryOperation
}) {
  const firstLog = detail?.logs[0]
  const targetHref = operation.target ? resourceHref(operation.target) : null
  const stepClass = 'flex flex-col gap-0.5 border-border border-l-2 px-3 py-1.5'
  const stepCurrent = 'border-primary bg-accent/40'
  return (
    <nav
      aria-label="Failure investigation"
      className="flex flex-col gap-1 px-3 py-3"
    >
      <span className={cn(stepClass, stepCurrent)} data-current="true">
        <small className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
          1 · Failure
        </small>
        <strong className="text-foreground text-[11px]">
          {operation.name}
        </strong>
      </span>
      <Link
        className={cn(stepClass, 'hover:bg-accent/50 transition-colors')}
        search={{ runId: operation.id }}
        to="/runs"
      >
        <small className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
          2 · Run
        </small>
        <strong className="text-foreground text-[11px]">
          Execution evidence
        </strong>
      </Link>
      {firstLog ? (
        <Link
          className={cn(stepClass, 'hover:bg-accent/50 transition-colors')}
          search={{ logId: firstLog.id }}
          to="/logs"
        >
          <small className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
            3 · Logs
          </small>
          <strong className="text-foreground text-[11px]">
            {firstLog.level} evidence
          </strong>
        </Link>
      ) : (
        <Link
          className={cn(stepClass, 'hover:bg-accent/50 transition-colors')}
          to="/logs"
        >
          <small className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
            3 · Logs
          </small>
          <strong className="text-foreground text-[11px]">
            Event evidence
          </strong>
        </Link>
      )}
      {targetHref ? (
        <Link
          className={cn(stepClass, 'hover:bg-accent/50 transition-colors')}
          to={targetHref}
        >
          <small className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
            4 · Target
          </small>
          <strong className="text-foreground text-[11px]">
            {operation.target?.label}
          </strong>
        </Link>
      ) : (
        <span className={stepClass}>
          <small className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
            4 · Target
          </small>
          <strong className="text-foreground text-[11px]">Platform</strong>
        </span>
      )}
    </nav>
  )
}

function RecoveryCard({
  children,
  label,
}: {
  children: ReactNode
  label: string
}) {
  return (
    <div className="border-border flex flex-col gap-1 border-l-2 px-3 py-1.5">
      <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
        {label}
      </span>
      {children}
    </div>
  )
}

function OperationRecoveryPanel({
  detail,
  failure,
  operation,
  quality,
}: {
  detail: ObservatoryOperationDetail | null
  failure: ReturnType<typeof operationFailure>
  operation: ObservatoryOperation
  quality: Array<ObservatoryQualityCheck>
}) {
  const related = detail?.related ?? []
  const logs = detail?.logs ?? []
  const enabledAction = (detail?.actions ?? []).find((action) => action.enabled)
  return (
    <div className="flex flex-col gap-3">
      <RecoveryCard label="Next action">
        <strong className="text-foreground text-[11px]">
          {operationNextAction(operation, enabledAction?.label)}
        </strong>
        <span className="text-muted-foreground text-[10px]/relaxed">
          {operationNextActionReason(operation, failure, enabledAction?.reason)}
        </span>
      </RecoveryCard>
      {quality.length > 0 && (
        <RecoveryCard label="Quality triage">
          {quality.slice(0, 3).map((check) => (
            <Link
              className="text-foreground text-[11px] hover:underline"
              key={check.id}
              search={{ checkId: check.id }}
              to="/quality"
            >
              {check.name}
            </Link>
          ))}
          <span className="text-muted-foreground text-[10px]">
            {quality.length} active check{quality.length === 1 ? '' : 's'} for{' '}
            {operation.target?.label ?? operation.target?.id ?? 'this target'}.
          </span>
        </RecoveryCard>
      )}
      <RecoveryCard label="Related resource">
        {(related.length > 0
          ? related
          : operation.target
            ? [operation.target]
            : []
        )
          .slice(0, 3)
          .map((resource) => (
            <Link
              className="text-foreground text-[11px] hover:underline"
              to={resourceHref(resource)}
              key={`${resource.kind}:${resource.id}`}
            >
              {resource.label}
            </Link>
          ))}
        {related.length === 0 && !operation.target && (
          <strong className="text-foreground text-[11px]">
            No related resource
          </strong>
        )}
        <span className="text-muted-foreground text-[10px]">
          Open the affected Dataset, table, or lineage evidence.
        </span>
      </RecoveryCard>
      <RecoveryCard label="Linked logs">
        {logs.slice(0, 3).map((log) => (
          <Link
            className="text-foreground flex items-center gap-1.5 text-[11px] hover:underline"
            key={log.id}
            search={{ logId: log.id }}
            to="/logs"
          >
            <FileText className="size-3.5" />
            {log.message}
          </Link>
        ))}
        {logs.length === 0 && (
          <strong className="text-foreground text-[11px]">
            No linked logs
          </strong>
        )}
        <span className="text-muted-foreground text-[10px]">
          {logs.length} event{logs.length === 1 ? '' : 's'} attached to this
          operation.
        </span>
      </RecoveryCard>
    </div>
  )
}

function WapOperationFocus({ operation }: { operation: ObservatoryOperation }) {
  const metadata = operation.metadata
  const tables = wapTables(operation)
  const flow = wapOperationFlow(operation, tables)
  const branch = textMetric(metadata, 'branch') ?? operation.target?.label
  const fields = [
    ['Run', textMetric(metadata, 'run_id') ?? operation.id],
    ['Branch', branch],
    ['Source hash', textMetric(metadata, 'source_hash')],
    ['Target before', textMetric(metadata, 'target_hash_before')],
    ['Target after', textMetric(metadata, 'target_hash_after')],
    ['Completed', formatDateTime(operation.completed_at)],
  ].filter((field): field is [string, string] => Boolean(field[1]))

  return (
    <div className="flex flex-col gap-3">
      <SectionCard
        actions={<Badge variant="secondary">{operation.status}</Badge>}
        description={`${operation.status} · ${branch ?? 'branch unknown'} · ${tables.length} table${tables.length === 1 ? '' : 's'}`}
        title="Branch publish execution · Execution report"
      >
        <FactGrid className="p-3">
          {fields.map(([label, value]) => (
            <Fact key={label} label={label} value={value} />
          ))}
        </FactGrid>
      </SectionCard>
      <div className="grid grid-cols-[14rem_minmax(0,1fr)] gap-3 max-lg:grid-cols-1">
        <SectionCard title="Publish steps">
          <div className="divide-border divide-y">
            <div className="flex flex-col gap-0.5 px-3 py-2">
              <span className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
                Branch
              </span>
              <strong className="text-foreground text-[11px]">
                {branch ?? 'branch unknown'}
              </strong>
              <span className="text-muted-foreground font-mono text-[10px]">
                {textMetric(metadata, 'source_hash') ?? 'source unknown'}
              </span>
            </div>
            <div className="flex flex-col gap-0.5 px-3 py-2">
              <span className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
                Table
              </span>
              <strong className="text-foreground text-[11px]">
                {tables[0]?.name ?? 'table evidence missing'}
              </strong>
              <span className="text-muted-foreground font-mono text-[10px]">
                {tables[0]?.records
                  ? `${tables[0].records} rows`
                  : 'records unknown'}
              </span>
            </div>
            <div className="flex flex-col gap-0.5 px-3 py-2">
              <span className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
                Publish
              </span>
              <strong className="text-foreground text-[11px]">
                {operation.name}
              </strong>
              <span className="text-muted-foreground font-mono text-[10px]">
                {textMetric(metadata, 'target_hash_after') ?? 'target unknown'}
              </span>
            </div>
          </div>
        </SectionCard>
        <div className="bg-paper border-rule min-h-[20rem] border">
          <ObservatoryFlowCanvas edges={flow.edges} nodes={flow.nodes} />
        </div>
      </div>
      <SectionCard
        actions={<Badge variant="secondary">{tables.length}</Badge>}
        title="Affected tables"
      >
        <div className="divide-border divide-y">
          {tables.map((table) => (
            <div
              className="flex items-center justify-between gap-2 px-3 py-2"
              key={table.id}
            >
              <span className="text-foreground flex items-center gap-1.5 text-[11px]">
                <Database className="size-3.5" />
                {table.name}
              </span>
              <span className="text-muted-foreground font-mono text-[10px]">
                {[
                  table.namespace,
                  table.format,
                  table.records ? `${table.records} records` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </div>
          ))}
          {tables.length === 0 && (
            <p className="text-muted-foreground px-3 py-2 text-xs">
              Report has no table evidence. Branch and hash evidence are still
              shown.
            </p>
          )}
        </div>
      </SectionCard>
    </div>
  )
}

function OperationLine({
  operation,
  onSelect,
  selected,
}: {
  operation: ObservatoryOperation
  onSelect: (id: string) => void
  selected: boolean
}) {
  const failure = operationFailure(operation)
  return (
    <button
      className={cn(
        'hover:bg-accent/50 flex w-full items-start gap-2 px-3 py-2 text-left transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      data-active={selected}
      onClick={() => onSelect(operation.id)}
      type="button"
    >
      <span
        className="status-dot mt-1.5 flex-none"
        data-state={operation.health.state}
      />
      <div className="min-w-0 flex-1">
        <div className="text-foreground flex items-center gap-1.5 text-xs font-medium">
          <RotateCcw className="text-muted-foreground size-3.5" />
          {operation.name}
        </div>
        <div className="text-muted-foreground mt-0.5 font-mono text-[10px]">
          {humanizeLabel(operation.kind)} ·{' '}
          {operation.target?.label ?? 'platform'} ·{' '}
          {formatDateTime(operation.completed_at) ?? 'in progress'}
        </div>
        {failure && (
          <div className="text-status-error mt-0.5 font-mono text-[10px]">
            {failure.message}
          </div>
        )}
      </div>
      <Badge className="flex-none" variant="outline">
        {operation.status}
      </Badge>
    </button>
  )
}

function mergeOperations(
  primary: Array<ObservatoryOperation>,
  secondary: Array<ObservatoryOperation>,
): Array<ObservatoryOperation> {
  const merged = new Map<string, ObservatoryOperation>()
  for (const operation of [...primary, ...secondary]) {
    merged.set(operation.id, operation)
  }
  return Array.from(merged.values()).sort((left, right) =>
    operationTimestamp(right).localeCompare(operationTimestamp(left)),
  )
}

function operationTimestamp(operation: ObservatoryOperation): string {
  return operation.completed_at ?? operation.started_at ?? operation.id
}

function chooseDefaultOperation(
  operations: Array<ObservatoryOperation>,
): ObservatoryOperation | null {
  return (
    operations.find((operation) => operation.status === 'failed') ??
    operations.find((operation) => operation.status === 'running') ??
    operations[0] ??
    null
  )
}

function wapTables(operation: ObservatoryOperation): Array<{
  id: string
  name: string
  namespace: string | null
  format: string | null
  records: string | null
}> {
  const rawTables = operation.metadata.tables
  if (!Array.isArray(rawTables)) return []
  return rawTables
    .map((item) => {
      if (typeof item === 'string') {
        const namespace = item.includes('.')
          ? item.split('.').slice(0, -1).join('.')
          : null
        return {
          id: item,
          name: item.split('.').at(-1) ?? item,
          namespace,
          format: null,
          records: null,
        }
      }
      if (!item || typeof item !== 'object') return null
      const table = item as Record<string, unknown>
      const metadata =
        table.metadata && typeof table.metadata === 'object'
          ? (table.metadata as Record<string, unknown>)
          : {}
      const id = String(table.id ?? table.asset_id ?? table.name ?? '')
      if (!id) return null
      const name = String(table.name ?? id.split('.').at(-1) ?? id)
      return {
        id,
        name,
        namespace: textValue(table.namespace ?? table.schema_name),
        format: textValue(table.format),
        records: textValue(metadata.records ?? table.records),
      }
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item))
}

function wapOperationFlow(
  operation: ObservatoryOperation,
  tables: ReturnType<typeof wapTables>,
): { nodes: Array<ObservatoryFlowNode>; edges: Array<ObservatoryFlowEdge> } {
  const branch =
    textMetric(operation.metadata, 'branch') ??
    operation.target?.label ??
    'branch'
  const sourceHash = textMetric(operation.metadata, 'source_hash')
  const targetHash = textMetric(operation.metadata, 'target_hash_after')
  const tableNodes = tables.slice(0, 6).map(
    (table): ObservatoryFlowNode => ({
      id: `table:${table.id}`,
      kind: 'table',
      label: table.name,
      lane: 'table',
      metric: table.records ? `${table.records} rows` : undefined,
    }),
  )
  const nodes: Array<ObservatoryFlowNode> = [
    {
      id: 'branch',
      kind: 'branch',
      label: branch,
      lane: 'branch',
      metric: sourceHash ?? undefined,
    },
    ...tableNodes,
    {
      id: 'publish',
      kind: 'operation',
      label: operation.name,
      lane: 'publish',
      metric: targetHash ?? undefined,
    },
  ]
  const edges: Array<ObservatoryFlowEdge> =
    tableNodes.length > 0
      ? [
          ...tableNodes.map((table) => ({
            id: `branch:${table.id}`,
            source: 'branch',
            target: table.id,
            label: 'writes',
          })),
          ...tableNodes.map((table) => ({
            id: `${table.id}:publish`,
            source: table.id,
            target: 'publish',
            label: 'promotes',
          })),
        ]
      : [
          {
            id: 'branch:publish',
            source: 'branch',
            target: 'publish',
            label: 'promotes',
          },
        ]
  return { edges, nodes }
}

function buildOperationLedger(operations: Array<ObservatoryOperation>) {
  const groups = new Map<
    string,
    {
      id: string
      kind: string
      label: string
      operations: Array<ObservatoryOperation>
    }
  >()

  for (const operation of operations) {
    const target = operation.target
    const id = target?.id ?? operation.kind
    const existing = groups.get(id)
    if (existing) {
      existing.operations.push(operation)
      continue
    }
    groups.set(id, {
      id,
      kind: target?.kind ?? operation.kind,
      label: target?.label ?? operation.kind,
      operations: [operation],
    })
  }

  return Array.from(groups.values())
    .map((group) => {
      const sorted = group.operations
        .slice()
        .sort((left, right) =>
          operationTimestamp(right).localeCompare(operationTimestamp(left)),
        )
      const failed = sorted.filter(
        (operation) => operation.status === 'failed',
      ).length
      const running = sorted.filter(
        (operation) => operation.status === 'running',
      ).length
      const succeeded = sorted.filter(
        (operation) => operation.status === 'succeeded',
      ).length
      return {
        id: group.id,
        kind: group.kind,
        label: group.label,
        latest: sorted[0],
        failed,
        running,
        succeeded,
        lastSeen: operationTimestamp(sorted[0]) || null,
        state: failed > 0 ? 'error' : (sorted[0]?.health.state ?? 'unknown'),
      }
    })
    .slice(0, 8)
}

function operationLedgerSummary(item: {
  failed: number
  lastSeen: string | null
  running: number
  succeeded: number
}): string {
  const states = [
    item.failed > 0 ? `${item.failed} failed` : null,
    item.running > 0 ? `${item.running} running` : null,
    item.succeeded > 0 ? `${item.succeeded} recovered` : null,
  ].filter(Boolean)
  const seen = item.lastSeen ? shortDate(item.lastSeen) : null
  return [...states, seen].filter(Boolean).join(' · ')
}

function operationNextAction(
  operation: ObservatoryOperation,
  enabledLabel?: string,
): string {
  if (enabledLabel) return enabledLabel
  if (operation.status === 'failed') {
    return operation.target ? 'Inspect failed Dataset' : 'Inspect failed run'
  }
  if (operation.status === 'running') return 'Monitor run'
  return 'Review evidence'
}

function operationNextActionReason(
  operation: ObservatoryOperation,
  failure: ReturnType<typeof operationFailure>,
  enabledReason?: string | null,
): string {
  if (enabledReason) return enabledReason
  if (operation.status === 'failed') {
    const target = operation.target?.label ?? operation.target?.id
    if (target) {
      return failure?.message
        ? `${failure.message} Start with ${target}, then resolve linked quality and log evidence.`
        : `Start with ${target}, then resolve linked quality and log evidence.`
    }
    return failure?.message ?? 'Open the failed run evidence and linked logs.'
  }
  if (operation.status === 'succeeded') {
    return operation.health.message ?? 'No recovery action is needed.'
  }
  return (
    operation.health.message ?? 'Review the linked evidence before retrying.'
  )
}

function operationEvidenceFacts(
  operation: ObservatoryOperation,
): Array<{ label: string; value: string | number | boolean | null }> {
  return [
    { label: 'Operation id', value: operation.id },
    { label: 'Status', value: operation.status },
    { label: 'Started', value: formatDateTime(operation.started_at) },
    { label: 'Completed', value: formatDateTime(operation.completed_at) },
    {
      label: 'Duration',
      value: operation.duration_seconds
        ? `${operation.duration_seconds}s`
        : operation.status === 'running'
          ? 'running'
          : null,
    },
    {
      label: 'Experiment',
      value: readMetric(operation.metadata, 'experiment_id'),
    },
    { label: 'Plate', value: readMetric(operation.metadata, 'plate_id') },
  ].filter((fact) => fact.value !== null)
}

function operationInspectorFacts(
  operation: ObservatoryOperation,
): Array<{ label: string; value: string | number | boolean | null }> {
  return [
    { label: 'Status', value: operation.status },
    { label: 'Kind', value: humanizeLabel(operation.kind) },
    { label: 'Started', value: formatDateTime(operation.started_at) },
    { label: 'Completed', value: formatDateTime(operation.completed_at) },
    {
      label: 'Duration',
      value: operation.duration_seconds
        ? `${operation.duration_seconds}s`
        : operation.status === 'running'
          ? 'running'
          : null,
    },
    { label: 'Namespace', value: readMetric(operation.metadata, 'namespace') },
    {
      label: 'Tables',
      value: readMetric(operation.metadata, 'tables_processed'),
    },
    {
      label: 'Records',
      value: readMetric(operation.metadata, 'total_records'),
    },
    {
      label: 'Size',
      value: readMetric(operation.metadata, 'total_size_mb')
        ? `${readMetric(operation.metadata, 'total_size_mb')} MB`
        : null,
    },
  ].filter((fact) => fact.value !== null)
}

function operationFailure(
  operation: ObservatoryOperation,
): { title: string; message: string } | null {
  if (operation.status !== 'failed' && operation.health.state !== 'error') {
    return null
  }
  const exceptionType = textMetric(operation.metadata, 'exception_type')
  const message =
    firstTextMetric(operation.metadata, [
      'exception_message',
      'failure_reason',
      'error',
      'reason',
      'message',
    ]) ??
    operation.health.message ??
    'No failure message recorded.'

  return {
    title: exceptionType ? `${exceptionType} failure` : 'Failure reason',
    message,
  }
}

function operationPageDescription(operation: ObservatoryOperation): string {
  const target =
    operation.target?.label ?? operation.target?.id ?? 'environment'
  const state = operation.health.message ?? operation.status
  return `${humanizeLabel(operation.kind)} for ${target}: ${state}`
}

function operationMetadata(
  operation: ObservatoryOperation,
): Array<[string, NonNullable<unknown>]> {
  const keys = [
    'pipeline_step',
    'source',
    'file_count',
    'package_path',
    'exception_type',
    'exception_message',
  ]
  return keys
    .map((key) => [key, operation.metadata[key]] as const)
    .filter((entry): entry is [string, NonNullable<unknown>] =>
      Boolean(entry[1]),
    )
    .slice(0, 6)
}

function resourceHref(resource: ObservatoryResourceRef): string {
  if (resource.kind === 'dataset') {
    return `/datasets/${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'table') {
    return `/tables?tableId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'asset') {
    return `/lineage?assetId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'quality') {
    return `/quality?checkId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'operation') {
    return `/operations?operationId=${encodeURIComponent(resource.id)}`
  }
  return '/lineage'
}

function firstTextMetric(
  metadata: Record<string, NonNullable<unknown>>,
  keys: Array<string>,
): string | null {
  for (const key of keys) {
    const value = textMetric(metadata, key)
    if (value) return value
  }
  return null
}

function textMetric(
  metadata: Record<string, NonNullable<unknown>>,
  key: string,
): string | null {
  const value = metadata[key]
  return typeof value === 'string' && value.trim() ? value : null
}

function textValue(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value
  if (typeof value === 'number') return String(value)
  return null
}

function formatDateTime(value?: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date)} UTC`
}

function shortDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(date)
}

function humanizeKey(key: string): string {
  return key.replaceAll('_', ' ')
}

function humanizeLabel(value: string): string {
  const acronyms: Record<string, string> = {
    api: 'API',
    bi: 'BI',
    id: 'ID',
    sql: 'SQL',
    ui: 'UI',
    wap: 'WAP',
  }
  const words = humanizeKey(value).split(' ')
  return words
    .map((word, index) => {
      const acronym = acronyms[word.toLowerCase()]
      if (acronym) return acronym
      return index === 0
        ? `${word.charAt(0).toUpperCase()}${word.slice(1)}`
        : word.toLowerCase()
    })
    .join(' ')
}
