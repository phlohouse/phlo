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
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  invalidateCachedResources,
  readMetric,
  useLiveResource,
} from '@/observatory/routes/liveResource'

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

  return (
    <ObservatoryPage
      kicker="Operations"
      title={latest ? latest.name : 'Recovery activity'}
      description={
        selectedIsWap && latest
          ? 'Branch publish evidence, affected tables, and target hash movement.'
          : latest
            ? operationPageDescription(latest)
            : 'Recovery operations, affected scope, evidence, and supported next steps.'
      }
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${visibleOperations.length} operations`}
        </span>
      }
    >
      <section
        className={`phlo-observatory-command${
          selectedIsWap ? ' phlo-observatory-wap-operation-shell' : ''
        }`}
      >
        <div className="phlo-observatory-command-primary">
          {isLoading ? (
            <div className="phlo-observatory-operation-empty">
              <div>
                <span className="phlo-observatory-inspector-label">
                  Operations
                </span>
                <h2>Loading operations</h2>
                <p>
                  Reading live recovery, service, and maintenance operation
                  evidence.
                </p>
              </div>
            </div>
          ) : visibleOperations.length > 0 ? (
            <>
              {!selectedIsWap && (
                <div className="phlo-observatory-command-strip">
                  <Metric
                    icon={<CheckCircle2 className="size-4" />}
                    label="Recovered"
                    value={isLoading ? 'Loading' : recovered}
                  />
                  <Metric
                    icon={<ShieldAlert className="size-4" />}
                    label="Failed"
                    value={isLoading ? 'Loading' : failed}
                  />
                  <Metric
                    icon={<Clock3 className="size-4" />}
                    label="Last duration"
                    value={
                      latest?.duration_seconds
                        ? `${latest.duration_seconds}s`
                        : 'not reported'
                    }
                  />
                </div>
              )}
              {latest && selectedIsWap ? (
                <WapOperationFocus operation={latest} />
              ) : latest ? (
                <div
                  className="phlo-observatory-operation-focus"
                  data-state={latest.health.state}
                >
                  <div className="phlo-observatory-workspace-toolbar">
                    <span>Selected operation evidence</span>
                    <span className="phlo-observatory-pill">
                      {latest.status}
                    </span>
                  </div>
                  <div className="phlo-observatory-operation-focus-body">
                    <div className="phlo-observatory-operation-focus-main">
                      <span className="phlo-observatory-inspector-label">
                        {humanizeLabel(latest.kind)}
                      </span>
                      <h2>{latest.name}</h2>
                      <p>{latest.target?.label ?? 'Platform operation'}</p>
                      {selectedFailure && (
                        <div className="phlo-observatory-failure-callout">
                          <strong>{selectedFailure.title}</strong>
                          <span>{selectedFailure.message}</span>
                        </div>
                      )}
                    </div>
                    <dl className="phlo-observatory-operation-evidence-grid">
                      {operationEvidenceFacts(latest).map((fact) => (
                        <Fact
                          key={fact.label}
                          label={fact.label}
                          value={fact.value}
                        />
                      ))}
                    </dl>
                  </div>
                  {selectedMetadata.length > 0 && (
                    <div className="phlo-observatory-operation-metadata">
                      {selectedMetadata.map(([key, value]) => (
                        <span key={key}>
                          <strong>{humanizeKey(key)}</strong>
                          {String(value)}
                        </span>
                      ))}
                    </div>
                  )}
                  <InvestigationPath detail={detail.data} operation={latest} />
                </div>
              ) : null}
              {!selectedIsWap && (
                <div className="phlo-observatory-operation-ledger">
                  <div className="phlo-observatory-workspace-toolbar">
                    <span>Target ledger</span>
                    <span className="phlo-observatory-pill">
                      {ledger.length} targets
                    </span>
                  </div>
                  <div className="phlo-observatory-operation-ledger-grid">
                    {ledger.map((item) => (
                      <button
                        className="phlo-observatory-operation-ledger-card"
                        data-state={item.state}
                        key={item.id}
                        onClick={() => selectOperation(item.latest.id)}
                        type="button"
                      >
                        <span>{humanizeLabel(item.kind)}</span>
                        <strong>{item.label}</strong>
                        <small>{operationLedgerSummary(item)}</small>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {!selectedIsWap && (
                <>
                  <div className="phlo-observatory-workspace-toolbar">
                    <span>Activity stream</span>
                    <span className="phlo-observatory-pill">
                      showing {displayedOperations.length}
                      {hiddenOperationCount > 0
                        ? ` of ${visibleOperations.length}`
                        : ''}
                    </span>
                  </div>
                  <div className="phlo-observatory-timeline">
                    {displayedOperations.map((operation) => (
                      <OperationLine
                        key={operation.id}
                        onSelect={selectOperation}
                        operation={operation}
                        selected={operation.id === latest?.id}
                      />
                    ))}
                    {hiddenOperationCount > 0 && (
                      <div className="phlo-observatory-noise-row">
                        {hiddenOperationCount} older operations kept out of the
                        DOM. Use target selection to narrow the working set.
                      </div>
                    )}
                  </div>
                </>
              )}
            </>
          ) : (
            <div className="phlo-observatory-operation-empty">
              <div>
                <span className="phlo-observatory-inspector-label">
                  No Phlo operations recorded
                </span>
                <h2>Operational history is quiet.</h2>
                <p>
                  Dagster owns orchestration and materialization runs.
                  Observatory will show Phlo recovery, branch, service, and
                  maintenance operations here once phlo-api records them.
                </p>
              </div>
              <div className="phlo-observatory-detail-list">
                <div className="phlo-observatory-mini-row">
                  <span>Dagster runs</span>
                  <small>Managed in Dagster</small>
                </div>
                <div className="phlo-observatory-mini-row">
                  <span>Phlo operations</span>
                  <small>0 recorded</small>
                </div>
                <div className="phlo-observatory-mini-row">
                  <span>Recovery actions</span>
                  <small>No guarded action history yet</small>
                </div>
              </div>
            </div>
          )}
        </div>

        {!selectedIsWap && (
          <aside className="phlo-observatory-inspector">
            <div className="phlo-observatory-inspector-label">
              Selected operation
            </div>
            {latest ? (
              <>
                <h2>{latest.name}</h2>
                <p>{latest.target?.label ?? humanizeLabel(latest.kind)}</p>
                <dl className="phlo-observatory-facts">
                  {operationInspectorFacts(latest).map((fact) => (
                    <Fact
                      key={fact.label}
                      label={fact.label}
                      value={fact.value}
                    />
                  ))}
                </dl>
                <OperationRecoveryPanel
                  detail={detail.data}
                  failure={selectedFailure}
                  operation={latest}
                  quality={selectedQuality}
                />
                <div className="phlo-observatory-action-row">
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
                            invalidateCachedResources([
                              'observatory:operations',
                            ])
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
                {actionMessage && (
                  <div className="phlo-observatory-panel-footer">
                    {actionMessage}
                  </div>
                )}
                {selectedFailure && (
                  <div className="phlo-observatory-detail-list">
                    <div
                      className="phlo-observatory-mini-row"
                      data-state="error"
                    >
                      <span>{selectedFailure.title}</span>
                      <small>{selectedFailure.message}</small>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <>
                <h2>
                  {isLoading
                    ? 'Loading operation detail'
                    : 'No operation selected'}
                </h2>
                <p>
                  {isLoading
                    ? 'Reading live operation records and recovery evidence.'
                    : 'There are no operation records for this environment yet.'}
                </p>
              </>
            )}
            {detail.error && (
              <div className="phlo-observatory-panel-footer">
                {detail.error}
              </div>
            )}
            {result.error && (
              <div className="phlo-observatory-panel-footer">
                {result.error}
              </div>
            )}
            {!result.error && directResult?.error && (
              <div className="phlo-observatory-panel-footer">
                {directResult.error}
              </div>
            )}
          </aside>
        )}
      </section>
    </ObservatoryPage>
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
  return (
    <nav
      aria-label="Failure investigation"
      className="phlo-observatory-investigation-path"
    >
      <span className="phlo-observatory-investigation-step" data-current="true">
        <small>1 · Failure</small>
        <strong>{operation.name}</strong>
      </span>
      <Link search={{ runId: operation.id }} to="/runs">
        <small>2 · Run</small>
        <strong>Execution evidence</strong>
      </Link>
      {firstLog ? (
        <Link search={{ logId: firstLog.id }} to="/logs">
          <small>3 · Logs</small>
          <strong>{firstLog.level} evidence</strong>
        </Link>
      ) : (
        <Link to="/logs">
          <small>3 · Logs</small>
          <strong>Event evidence</strong>
        </Link>
      )}
      {targetHref ? (
        <Link to={targetHref}>
          <small>4 · Target</small>
          <strong>{operation.target?.label}</strong>
        </Link>
      ) : (
        <span className="phlo-observatory-investigation-step">
          <small>4 · Target</small>
          <strong>Platform</strong>
        </span>
      )}
    </nav>
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
    <div className="phlo-observatory-operation-recovery">
      <div className="phlo-observatory-operation-recovery-card">
        <span>Next action</span>
        <strong>{operationNextAction(operation, enabledAction?.label)}</strong>
        <small>
          {operationNextActionReason(operation, failure, enabledAction?.reason)}
        </small>
      </div>
      {quality.length > 0 && (
        <div className="phlo-observatory-operation-recovery-card">
          <span>Quality triage</span>
          {quality.slice(0, 3).map((check) => (
            <Link key={check.id} search={{ checkId: check.id }} to="/quality">
              {check.name}
            </Link>
          ))}
          <small>
            {quality.length} active check{quality.length === 1 ? '' : 's'} for{' '}
            {operation.target?.label ?? operation.target?.id ?? 'this target'}.
          </small>
        </div>
      )}
      <div className="phlo-observatory-operation-recovery-card">
        <span>Related resource</span>
        {(related.length > 0
          ? related
          : operation.target
            ? [operation.target]
            : []
        )
          .slice(0, 3)
          .map((resource) => (
            <Link
              to={resourceHref(resource)}
              key={`${resource.kind}:${resource.id}`}
            >
              {resource.label}
            </Link>
          ))}
        {related.length === 0 && !operation.target && (
          <strong>No related resource</strong>
        )}
        <small>Open the affected Dataset, table, or lineage evidence.</small>
      </div>
      <div className="phlo-observatory-operation-recovery-card">
        <span>Linked logs</span>
        {logs.slice(0, 3).map((log) => (
          <Link key={log.id} search={{ logId: log.id }} to="/logs">
            <FileText className="size-3.5" />
            {log.message}
          </Link>
        ))}
        {logs.length === 0 && <strong>No linked logs</strong>}
        <small>
          {logs.length} event{logs.length === 1 ? '' : 's'} attached to this
          operation.
        </small>
      </div>
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
    <div className="phlo-observatory-wap-operation">
      <div className="phlo-observatory-wap-operation-header">
        <div>
          <span className="phlo-observatory-inspector-label">
            Branch publish execution
          </span>
          <strong>Execution report</strong>
          <p>
            {operation.status} · {branch ?? 'branch unknown'} · {tables.length}{' '}
            table{tables.length === 1 ? '' : 's'}
          </p>
        </div>
        <span className="phlo-observatory-pill">{operation.status}</span>
      </div>
      <dl className="phlo-observatory-wap-operation-fields">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="phlo-observatory-wap-operation-evidence">
        <div className="phlo-observatory-wap-operation-steps">
          <div>
            <span>Branch</span>
            <strong>{branch ?? 'branch unknown'}</strong>
            <small>
              {textMetric(metadata, 'source_hash') ?? 'source unknown'}
            </small>
          </div>
          <div>
            <span>Table</span>
            <strong>{tables[0]?.name ?? 'table evidence missing'}</strong>
            <small>
              {tables[0]?.records
                ? `${tables[0].records} rows`
                : 'records unknown'}
            </small>
          </div>
          <div>
            <span>Publish</span>
            <strong>{operation.name}</strong>
            <small>
              {textMetric(metadata, 'target_hash_after') ?? 'target unknown'}
            </small>
          </div>
        </div>
        <div className="phlo-observatory-wap-operation-flow">
          <ObservatoryFlowCanvas edges={flow.edges} nodes={flow.nodes} />
        </div>
        <div className="phlo-observatory-wap-operation-tables">
          <div className="phlo-observatory-workspace-toolbar">
            <span>Affected tables</span>
            <span className="phlo-observatory-pill">{tables.length}</span>
          </div>
          {tables.map((table) => (
            <div className="phlo-observatory-mini-row" key={table.id}>
              <span>
                <Database className="size-3.5" />
                {table.name}
              </span>
              <small>
                {[
                  table.namespace,
                  table.format,
                  table.records ? `${table.records} records` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </small>
            </div>
          ))}
          {tables.length === 0 && (
            <p>
              Report has no table evidence. Branch and hash evidence are still
              shown.
            </p>
          )}
        </div>
      </div>
    </div>
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
      className="phlo-observatory-timeline-row"
      data-active={selected}
      onClick={() => onSelect(operation.id)}
      type="button"
    >
      <span
        className="phlo-observatory-dot"
        data-state={operation.health.state}
      />
      <div>
        <div className="phlo-observatory-row-title">
          <RotateCcw className="size-4" />
          {operation.name}
        </div>
        <div className="phlo-observatory-row-meta">
          {humanizeLabel(operation.kind)} ·{' '}
          {operation.target?.label ?? 'platform'} ·{' '}
          {formatDateTime(operation.completed_at) ?? 'in progress'}
        </div>
        {failure && (
          <div className="phlo-observatory-row-meta phlo-observatory-row-evidence">
            {failure.message}
          </div>
        )}
      </div>
      <span className="phlo-observatory-pill">{operation.status}</span>
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
