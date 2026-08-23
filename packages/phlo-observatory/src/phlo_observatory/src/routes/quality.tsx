/**
 * /quality route. Quality check matrix per dataset with check detail,
 * dataset profile inspection, and run-check actions.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  AlertTriangle,
  CircleHelp,
  ClipboardCheck,
  Database,
  ExternalLink,
  PlayCircle,
  Shield,
  ShieldCheck,
  TerminalSquare,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryDataset,
  ObservatoryDatasetProfile,
  ObservatoryQualityCheck,
  ObservatoryQualityDetail,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import type {
  ObservatoryFlowEdge,
  ObservatoryFlowNode,
} from '@/observatory/components/ObservatoryFlowCanvas'
import {
  getObservatoryDatasetProfileDirect,
  getObservatoryDatasetRecords,
  getObservatoryQualityDetail,
  getObservatoryQualityDetailDirect,
  getObservatoryQualityRecords,
  runObservatoryAction,
} from '@/observatory/api/resources'
import { ActionButton } from '@/observatory/components/ActionButton'
import { ObservatoryFlowCanvas } from '@/observatory/components/ObservatoryFlowCanvas'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  invalidateCachedResources,
  loadCachedResource,
  useLiveResource,
} from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/quality')({
  component: Quality,
})

export function Quality() {
  const result = useLiveResource(
    getObservatoryQualityRecords,
    120_000,
    'observatory:quality',
  )
  const datasetResult = useLiveResource(
    getObservatoryDatasetRecords,
    120_000,
    'observatory:datasets',
  )
  const checks = result.data ?? []
  const datasets = datasetResult.data ?? []
  const isLoading = result.isLoading
  const sortedChecks = useMemo(() => [...checks].sort(compareQuality), [checks])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activeView, setActiveView] = useState<QualityView>('queue')
  const blocking = checks.filter(
    (check) => check.blocking && check.status !== 'passing',
  ).length
  const warnings = checks.filter((check) => check.status === 'warning').length
  const unknown = checks.filter((check) => check.status === 'unknown').length
  const failing = checks.filter((check) => check.status === 'failing').length
  const observed = checks.length - unknown
  const score = observed
    ? Math.round(((observed - failing) / observed) * 100)
    : null
  const selected =
    sortedChecks.find((check) => check.id === selectedId) ??
    sortedChecks[0] ??
    null
  const [detail, setDetail] = useState<
    ObservatoryResourceResult<ObservatoryQualityDetail>
  >({
    data: null,
    error: null,
  })
  const [datasetProfile, setDatasetProfile] = useState<
    ObservatoryResourceResult<ObservatoryDatasetProfile>
  >({
    data: null,
    error: null,
  })
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const graph = useMemo(() => buildQualityGraph(sortedChecks), [sortedChecks])
  const selectedDetail =
    detail.data?.check.id === selected?.id ? detail.data : null
  const selectedDatasetTarget = useMemo(
    () =>
      selected
        ? qualityDatasetTarget(selected, selectedDetail, datasets)
        : null,
    [datasets, selected, selectedDetail],
  )
  const selectedDatasetProfile =
    datasetProfile.data?.dataset.id === selectedDatasetTarget?.id
      ? datasetProfile.data
      : null

  const selectCheck = useCallback((id: string) => {
    setSelectedId(id)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('checkId', id)
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get('checkId')
    if (!requested || requested === selectedId) return
    if (sortedChecks.some((check) => check.id === requested)) {
      setSelectedId(requested)
    }
  }, [selectedId, sortedChecks])

  useEffect(() => {
    if (selectedId !== null || sortedChecks.length === 0) return
    const requested =
      typeof window === 'undefined'
        ? null
        : new URLSearchParams(window.location.search).get('checkId')
    const initial =
      sortedChecks.find((check) => check.id === requested) ?? sortedChecks[0]
    selectCheck(initial.id)
  }, [selectCheck, selectedId, sortedChecks])

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    const loadDetail =
      typeof window === 'undefined'
        ? () => getObservatoryQualityDetail({ data: { checkId: selected.id } })
        : () => getObservatoryQualityDetailDirect({ checkId: selected.id })

    void loadCachedResource(
      `observatory:quality-detail:${selected.id}`,
      loadDetail,
      {
        staleMs: 120_000,
      },
    ).then((next) => {
      if (!cancelled) setDetail(next)
    })
    return () => {
      cancelled = true
    }
  }, [selected])

  useEffect(() => {
    if (!selectedDatasetTarget) {
      setDatasetProfile({ data: null, error: null })
      return
    }
    let cancelled = false
    setDatasetProfile({ data: null, error: null })
    void loadCachedResource(
      `observatory:dataset-profile:${selectedDatasetTarget.id}`,
      () =>
        getObservatoryDatasetProfileDirect({
          datasetId: selectedDatasetTarget.id,
        }),
      { staleMs: 120_000 },
    ).then((next) => {
      if (!cancelled) setDatasetProfile(next)
    })
    return () => {
      cancelled = true
    }
  }, [selectedDatasetTarget])

  return (
    <ObservatoryPage
      kicker="Quality"
      title="Quality triage"
      description="Resolve the exact checks blocking trust, publication, and downstream use."
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${checks.length} checks`}
        </span>
      }
    >
      <section className="phlo-observatory-quality-shell">
        <div className="phlo-observatory-quality-board">
          {selected && (
            <SelectedQualityWorkbench
              detail={selectedDetail}
              selected={selected}
              target={selectedDatasetTarget}
            />
          )}
          <div className="phlo-observatory-quality-command">
            <div className="phlo-observatory-quality-score">
              <strong>
                {isLoading ? 'Loading' : score === null ? '—' : score}
              </strong>
              <span>Observed health</span>
              <small>
                {isLoading
                  ? 'Reading live quality evidence'
                  : `${observed} observed · ${failing} failing · ${unknown} pending`}
              </small>
            </div>
            <div className="phlo-observatory-command-strip">
              <Metric
                icon={<Shield className="size-4" />}
                label="Blocking"
                value={isLoading ? 'Loading' : blocking}
              />
              <Metric
                icon={<AlertTriangle className="size-4" />}
                label="Warnings"
                value={isLoading ? 'Loading' : warnings}
              />
              <Metric
                icon={<CircleHelp className="size-4" />}
                label="Not observed"
                value={isLoading ? 'Loading' : unknown}
              />
            </div>
          </div>
          <div className="phlo-observatory-data-main-tabs" role="tablist">
            {qualityViews.map((view) => (
              <button
                aria-selected={activeView === view.id}
                data-active={activeView === view.id}
                key={view.id}
                onClick={() => setActiveView(view.id)}
                role="tab"
                type="button"
              >
                {view.icon}
                {view.label}
              </button>
            ))}
          </div>
          {activeView === 'graph' ? (
            <div className="phlo-observatory-flow-band">
              <div className="phlo-observatory-workspace-toolbar">
                <span>Quality dependencies</span>
                <span className="phlo-observatory-pill">
                  {isLoading ? 'Loading' : `${graph.edges.length} bindings`}
                </span>
              </div>
              <ObservatoryFlowCanvas
                edges={graph.edges}
                nodes={graph.nodes}
                onSelect={selectCheck}
                selectedId={selected?.id}
              />
            </div>
          ) : (
            <>
              <div className="phlo-observatory-quality-table-head">
                <span>Check</span>
                <span>Impact</span>
                <span>Evidence</span>
                <span>Next</span>
              </div>
              <div className="phlo-observatory-check-list">
                {sortedChecks.map((check) => (
                  <CheckRow
                    key={check.id}
                    check={check}
                    detail={check.id === selected?.id ? selectedDetail : null}
                    onSelect={selectCheck}
                    selected={check.id === selected?.id}
                  />
                ))}
                {isLoading ? (
                  <div className="phlo-observatory-empty-state">
                    Reading live quality checks and evidence.
                  </div>
                ) : (
                  checks.length === 0 && (
                    <div className="phlo-observatory-empty-state">
                      No quality checks registered yet.
                    </div>
                  )
                )}
              </div>
            </>
          )}
        </div>

        <aside className="phlo-observatory-inspector">
          <div className="phlo-observatory-inspector-label">
            Triage evidence
          </div>
          {selected ? (
            <>
              <div
                className="phlo-observatory-quality-triage-summary"
                data-state={qualityVisualState(selected)}
              >
                <div>
                  <h2>{selected.name}</h2>
                  <p>
                    {selected.description ??
                      `Dataset ${qualityDatasetLabel(selected, selectedDetail)}`}
                  </p>
                </div>
                <span className="phlo-observatory-pill">
                  {qualityStatusLabel(selected)}
                </span>
              </div>
              <dl className="phlo-observatory-facts">
                <Fact
                  label="Dataset"
                  value={qualityDatasetLabel(selected, selectedDetail)}
                />
                <Fact
                  label="Severity"
                  value={selected.severity ?? 'unspecified'}
                />
                <Fact
                  label="Blocking"
                  value={selected.blocking ? 'yes' : 'no'}
                />
                <Fact label="Status" value={qualityStatusLabel(selected)} />
                <Fact
                  label="Owner"
                  value={readQualityOwner(selected, selectedDetail)}
                />
              </dl>
              <div className="phlo-observatory-quality-next-step">
                <span>Next action</span>
                <strong>{qualityNextAction(selected, selectedDetail)}</strong>
                <small>
                  {qualityNextActionReason(selected, selectedDetail)}
                </small>
              </div>
              <DatasetReadinessContext
                profile={selectedDatasetProfile}
                selected={selected}
                target={selectedDatasetTarget}
              />
              <QualityEvidence
                detail={selectedDetail}
                selected={selected}
                target={selectedDatasetTarget}
              />
              <QualityHistory detail={selectedDetail} selected={selected} />
              <QualityNextActions
                detail={selectedDetail}
                selected={selected}
                target={selectedDatasetTarget}
              />
              {(selectedDetail?.actions ?? []).length > 0 && (
                <div className="phlo-observatory-action-row">
                  {(selectedDetail?.actions ?? []).map((action) => (
                    <ActionButton
                      action={action}
                      key={action.id}
                      onRun={(actionId) => {
                        void runObservatoryAction({ data: { actionId } }).then(
                          (next) => {
                            invalidateCachedResources([
                              'observatory:operations',
                              'observatory:quality',
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
              )}
              {actionMessage && (
                <div className="phlo-observatory-panel-footer">
                  {actionMessage}
                </div>
              )}
            </>
          ) : (
            <p>
              {isLoading
                ? 'Loading selected quality check and triage evidence.'
                : 'No quality check selected.'}
            </p>
          )}
          {detail.error && (
            <div className="phlo-observatory-panel-footer">{detail.error}</div>
          )}
          {datasetProfile.error && (
            <div className="phlo-observatory-panel-footer">
              Dataset readiness context is unavailable: {datasetProfile.error}
            </div>
          )}
          {result.error && (
            <div className="phlo-observatory-panel-footer">{result.error}</div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function DatasetReadinessContext({
  profile,
  selected,
  target,
}: {
  profile: ObservatoryDatasetProfile | null
  selected: ObservatoryQualityCheck
  target: QualityDatasetTarget | null
}) {
  const failingControls =
    profile?.governance.filter((control) => control.status === 'fail') ?? []
  const blockers = profile?.publishing.blockers ?? []
  const missingEvidence = profile?.publishing.missing_evidence ?? []
  const warnings = profile?.publishing.warnings ?? []
  const dataset = profile?.dataset

  return (
    <div className="phlo-observatory-quality-readiness">
      <div className="phlo-observatory-quality-readiness-header">
        <span>Dataset readiness</span>
        {target ? (
          <Link params={{ datasetId: target.id }} to="/datasets/$datasetId">
            Open Dataset
          </Link>
        ) : (
          <Link to={qualityLineageHref(selected)}>Open Lineage</Link>
        )}
      </div>
      {profile && dataset ? (
        <>
          <div className="phlo-observatory-quality-readiness-grid">
            <ReadinessFact
              label="Publication"
              value={dataset.publication_state}
            />
            <ReadinessFact label="Readiness" value={dataset.readiness_state} />
            <ReadinessFact
              label="Owner"
              value={dataset.owner ?? 'unassigned'}
            />
            <ReadinessFact
              label="Classifications"
              value={
                dataset.classifications.length
                  ? dataset.classifications.join(', ')
                  : 'unassigned'
              }
            />
          </div>
          <div className="phlo-observatory-detail-list">
            {blockers.slice(0, 3).map((blocker) => (
              <div className="phlo-observatory-mini-row" key={blocker}>
                <span>{blocker}</span>
                <small>release blocker</small>
              </div>
            ))}
            {missingEvidence
              .slice(0, 3 - blockers.slice(0, 3).length)
              .map((item) => (
                <div
                  className="phlo-observatory-mini-row"
                  data-state="unknown"
                  key={item}
                >
                  <span>{item}</span>
                  <small>missing evidence</small>
                </div>
              ))}
            {warnings
              .slice(
                0,
                Math.max(0, 3 - blockers.length - missingEvidence.length),
              )
              .map((warning) => (
                <div
                  className="phlo-observatory-mini-row"
                  data-state="warning"
                  key={warning}
                >
                  <span>{warning}</span>
                  <small>warning</small>
                </div>
              ))}
            {blockers.length === 0 &&
              missingEvidence.length === 0 &&
              warnings.length === 0 &&
              failingControls.length === 0 && (
                <div className="phlo-observatory-mini-row">
                  <span>Ready for publication controls</span>
                  <small>{profile.publishing.policy_name}</small>
                </div>
              )}
            {failingControls
              .filter((control) => !blockers.includes(control.message ?? ''))
              .slice(0, 2)
              .map((control) => (
                <div className="phlo-observatory-mini-row" key={control.id}>
                  <span>{control.label}</span>
                  <small>{control.message ?? control.status}</small>
                </div>
              ))}
          </div>
        </>
      ) : (
        <div className="phlo-observatory-mini-row">
          <span>
            {target ? 'Loading readiness context' : 'No Dataset binding found'}
          </span>
          <small>
            {target?.label ??
              'Use lineage evidence to bind this resource to a Dataset.'}
          </small>
        </div>
      )}
    </div>
  )
}

function ReadinessFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function QualityEvidence({
  detail,
  selected,
  target,
}: {
  detail: ObservatoryQualityDetail | null
  selected: ObservatoryQualityCheck
  target: QualityDatasetTarget | null
}) {
  const history = detail?.history ?? []
  const logs = detail?.logs ?? []
  const latestRun = history[0]
  const latestLog = logs[0]
  return (
    <div className="phlo-observatory-quality-evidence">
      <div className="phlo-observatory-quality-evidence-card">
        <span>Impact</span>
        <strong>{qualityImpact(selected, detail)}</strong>
        <small>
          {urgentReason(selected) ?? qualityResultSummary(selected)}
        </small>
      </div>
      <div className="phlo-observatory-quality-evidence-card">
        <span>Related run</span>
        {latestRun ? (
          <a
            href={`/operations?operationId=${encodeURIComponent(latestRun.id)}`}
          >
            {latestRun.name}
          </a>
        ) : (
          <strong>No linked run</strong>
        )}
        <small>
          {latestRun
            ? `${latestRun.status} · ${formatDateTime(latestRun.completed_at)}`
            : 'No run evidence is linked to this check yet.'}
        </small>
      </div>
      <div className="phlo-observatory-quality-evidence-card">
        <span>Latest log</span>
        {latestLog ? (
          <a href={`/logs?logId=${encodeURIComponent(latestLog.id)}`}>
            {latestLog.message}
          </a>
        ) : (
          <strong>No linked log</strong>
        )}
        <small>
          {latestLog
            ? `${latestLog.level} · ${formatDateTime(latestLog.timestamp)}`
            : 'No log evidence is linked to this check yet.'}
        </small>
      </div>
      <div className="phlo-observatory-quality-evidence-card">
        <span>Evidence depth</span>
        <strong>
          {history.length} runs · {logs.length} logs
        </strong>
        <small>{qualityActionsSummary(detail)}</small>
      </div>
      {target ? (
        <Link
          className="phlo-observatory-quality-evidence-card"
          params={{ datasetId: target.id }}
          to="/datasets/$datasetId"
        >
          <span>Dataset profile</span>
          <strong>{target.label}</strong>
          <small>Readiness, ownership, publication, and controls.</small>
        </Link>
      ) : (
        <a
          className="phlo-observatory-quality-evidence-card"
          href={qualityLineageHref(selected)}
        >
          <span>Source binding</span>
          <strong>{detail?.asset?.name ?? selected.asset_id}</strong>
          <small>Bind this source before treating it as a Dataset.</small>
        </a>
      )}
    </div>
  )
}

function QualityHistory({
  detail,
  selected,
}: {
  detail: ObservatoryQualityDetail | null
  selected: ObservatoryQualityCheck
}) {
  const history = detail?.history ?? []
  const logs = detail?.logs ?? []

  return (
    <div className="phlo-observatory-quality-history">
      <span className="phlo-observatory-inspector-label">History</span>
      <div className="phlo-observatory-detail-list">
        {history.slice(0, 3).map((run) => (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            key={run.id}
            search={{ operationId: run.id }}
            to="/operations"
          >
            <span>{run.name}</span>
            <small>
              {run.status} · {formatDateTime(run.completed_at)}
            </small>
          </Link>
        ))}
        {logs.slice(0, Math.max(0, 3 - history.length)).map((log) => (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            key={log.id}
            search={{ logId: log.id }}
            to="/logs"
          >
            <span>{log.message}</span>
            <small>
              {log.level} · {formatDateTime(log.timestamp)}
            </small>
          </Link>
        ))}
        {history.length === 0 && logs.length === 0 && (
          <div className="phlo-observatory-mini-row" data-state="unknown">
            <span>No attached history yet</span>
            <small>{selected.asset_id}</small>
          </div>
        )}
      </div>
    </div>
  )
}

function QualityNextActions({
  detail,
  selected,
  target,
}: {
  detail: ObservatoryQualityDetail | null
  selected: ObservatoryQualityCheck
  target: QualityDatasetTarget | null
}) {
  const latestRun = detail?.history[0]
  const latestLog = detail?.logs[0]
  const enabledAction = (detail?.actions ?? []).find((action) => action.enabled)
  const disabledReason = (detail?.actions ?? []).find(
    (action) => !action.enabled && action.reason,
  )?.reason

  return (
    <div className="phlo-observatory-quality-next-actions">
      <span className="phlo-observatory-inspector-label">Next actions</span>
      <div className="phlo-observatory-detail-list">
        {enabledAction ? (
          <div className="phlo-observatory-mini-row" data-state="ok">
            <span>{enabledAction.label}</span>
            <small>
              {[
                'available now',
                enabledAction.risk_level
                  ? `${enabledAction.risk_level} risk`
                  : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </small>
          </div>
        ) : (
          <div className="phlo-observatory-mini-row" data-state="unknown">
            <span>{qualityNextActionShort(selected, detail)}</span>
            <small>
              {disabledReason ?? qualityNextActionReason(selected, detail)}
            </small>
          </div>
        )}
        {latestRun && (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            search={{ operationId: latestRun.id }}
            to="/operations"
          >
            <span>Open related run</span>
            <small>{latestRun.name}</small>
          </Link>
        )}
        {latestLog && (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            search={{ logId: latestLog.id }}
            to="/logs"
          >
            <span>Open latest log</span>
            <small>{latestLog.level}</small>
          </Link>
        )}
        {target ? (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            params={{ datasetId: target.id }}
            to="/datasets/$datasetId"
          >
            <span>Open affected Dataset</span>
            <small>{target.label}</small>
          </Link>
        ) : (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            to={qualityLineageHref(selected)}
          >
            <span>Open lineage binding</span>
            <small>{selected.asset_id}</small>
          </Link>
        )}
      </div>
    </div>
  )
}

function SelectedQualityWorkbench({
  detail,
  selected,
  target,
}: {
  detail: ObservatoryQualityDetail | null
  selected: ObservatoryQualityCheck
  target: QualityDatasetTarget | null
}) {
  const latestRun = detail?.history[0]
  const latestLog = detail?.logs[0]
  const datasetLabel =
    readAssetMetadata(detail?.asset, 'dataset_name') ??
    detail?.asset?.name ??
    selected.asset_id
  const resourceHref =
    target === null
      ? qualityLineageHref(selected)
      : `/datasets/${encodeURIComponent(target.id)}`

  return (
    <div
      className="phlo-observatory-quality-workbench"
      data-state={qualityVisualState(selected)}
    >
      <div className="phlo-observatory-quality-workbench-title">
        <div>
          <span className="phlo-observatory-dot-label">
            <span
              className="phlo-observatory-dot"
              data-state={qualityVisualState(selected)}
            />
            {qualityStatusLabel(selected)}
          </span>
          <h2>{selected.name}</h2>
          <p>{qualityImpact(selected, detail)}</p>
        </div>
        <a className="phlo-observatory-action-link" href={resourceHref}>
          <Database className="size-3.5" />
          {target?.label ?? datasetLabel}
        </a>
      </div>
      <div className="phlo-observatory-quality-workbench-grid">
        <WorkbenchCell
          icon={<ClipboardCheck className="size-4" />}
          label="Why it matters"
          title={qualityImpactShort(selected, detail)}
          detail={selected.description ?? qualityResultSummary(selected)}
        />
        <WorkbenchCell
          icon={<PlayCircle className="size-4" />}
          label="Related run"
          title={latestRun?.name ?? 'No linked run'}
          detail={
            latestRun
              ? `${latestRun.status} · ${formatDateTime(latestRun.completed_at)}`
              : 'No run history is linked to this check.'
          }
          href={
            latestRun
              ? `/operations?operationId=${encodeURIComponent(latestRun.id)}`
              : undefined
          }
        />
        <WorkbenchCell
          icon={<TerminalSquare className="size-4" />}
          label="Latest log"
          title={latestLog?.message ?? 'No linked log'}
          detail={
            latestLog
              ? `${latestLog.source} · ${formatDateTime(latestLog.timestamp)}`
              : 'No log event is linked to this check.'
          }
          href={
            latestLog
              ? `/logs?logId=${encodeURIComponent(latestLog.id)}`
              : undefined
          }
        />
        <WorkbenchCell
          icon={<ShieldCheck className="size-4" />}
          label="Next action"
          title={qualityNextAction(selected, detail)}
          detail={qualityNextActionReason(selected, detail)}
        />
      </div>
    </div>
  )
}

function WorkbenchCell({
  detail,
  href,
  icon,
  label,
  title,
}: {
  detail: string
  href?: string
  icon: ReactNode
  label: string
  title: string
}) {
  const content = (
    <>
      <span>
        {icon}
        {label}
      </span>
      <strong>{title}</strong>
      <small>{detail}</small>
      {href && <ExternalLink className="phlo-observatory-cell-link-icon" />}
    </>
  )

  if (href) {
    return (
      <a className="phlo-observatory-quality-workbench-cell" href={href}>
        {content}
      </a>
    )
  }

  return (
    <div className="phlo-observatory-quality-workbench-cell">{content}</div>
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

type QualityView = 'queue' | 'graph'

const qualityViews: Array<{
  id: QualityView
  label: string
  icon: ReactNode
}> = [
  { id: 'queue', label: 'Queue', icon: <ShieldCheck className="size-3.5" /> },
  {
    id: 'graph',
    label: 'Dependencies',
    icon: <AlertTriangle className="size-3.5" />,
  },
]

function CheckRow({
  check,
  detail,
  onSelect,
  selected,
}: {
  check: ObservatoryQualityCheck
  detail: ObservatoryQualityDetail | null
  onSelect: (id: string) => void
  selected: boolean
}) {
  return (
    <button
      className="phlo-observatory-check-row"
      data-active={selected}
      onClick={() => onSelect(check.id)}
      type="button"
    >
      <span
        className="phlo-observatory-dot"
        data-state={
          check.status === 'failing'
            ? 'error'
            : check.status === 'unknown'
              ? 'warning'
              : check.status
        }
      />
      <div>
        <div className="phlo-observatory-row-title">
          <ShieldCheck className="size-4" />
          {check.name}
        </div>
        <div className="phlo-observatory-row-meta">
          {qualityDatasetLabel(check, detail)} · {qualityStatusLabel(check)} ·{' '}
          {check.severity ?? 'severity unset'} ·{' '}
          {check.blocking ? 'blocking' : 'advisory'}
        </div>
      </div>
      <span className="phlo-observatory-quality-cell">
        {qualityImpactShort(check, detail)}
      </span>
      <span className="phlo-observatory-quality-cell">
        {qualityEvidenceSummary(check, detail)}
      </span>
      <span className="phlo-observatory-quality-cell">
        {qualityNextActionShort(check, detail)}
      </span>
    </button>
  )
}

function buildQualityGraph(checks: Array<ObservatoryQualityCheck>): {
  nodes: Array<ObservatoryFlowNode>
  edges: Array<ObservatoryFlowEdge>
} {
  const assetNodes = Array.from(
    new Set(checks.map((check) => check.asset_id)),
  ).map(
    (asset): ObservatoryFlowNode => ({
      id: `asset:${asset}`,
      label: qualityDatasetLabel(
        checks.find((check) => check.asset_id === asset) ?? checks[0],
        null,
      ),
      kind: 'asset',
      lane: 'table',
      selectId: checks.find((check) => check.asset_id === asset)?.id,
      subtitle: 'protected dataset',
    }),
  )

  const checkNodes = checks.map(
    (check): ObservatoryFlowNode => ({
      id: check.id,
      label: check.name,
      kind: 'quality',
      lane: 'quality',
      subtitle: qualityDatasetLabel(check, null),
      metric: `${check.severity ?? qualityStatusLabel(check)} · ${check.blocking ? 'blocking' : 'advisory'}`,
    }),
  )

  const edges = checks.map(
    (check): ObservatoryFlowEdge => ({
      id: `${check.asset_id}->${check.id}`,
      source: `asset:${check.asset_id}`,
      target: check.id,
    }),
  )

  return { nodes: [...assetNodes, ...checkNodes], edges }
}

function compareQuality(
  left: ObservatoryQualityCheck,
  right: ObservatoryQualityCheck,
): number {
  return qualityUrgency(right) - qualityUrgency(left)
}

function qualityUrgency(check: ObservatoryQualityCheck): number {
  let score = 0
  if (check.status === 'failing') score += 100
  if (check.blocking) score += 30
  if (check.status === 'warning') score += 20
  if (check.status === 'unknown') score += 10
  if (check.severity === 'critical') score += 25
  if (check.severity === 'high') score += 15
  if (check.severity === 'medium') score += 5
  return score
}

function qualityStatusLabel(check: ObservatoryQualityCheck): string {
  if (check.status === 'unknown') return 'not observed'
  return check.status
}

function qualityVisualState(check: ObservatoryQualityCheck): string {
  if (check.status === 'failing') return 'error'
  if (check.status === 'warning' || check.status === 'unknown') return 'warning'
  return 'ok'
}

function qualityResultSummary(check: ObservatoryQualityCheck): string {
  if (check.status === 'unknown') {
    return 'No quality result has been recorded for this check yet.'
  }
  if (check.status === 'passing') return 'Latest observed run passed.'
  if (check.status === 'warning') return 'Latest observed run raised a warning.'
  return 'Latest observed run failed.'
}

function qualityActionsSummary(
  detail: ObservatoryQualityDetail | null,
): string {
  const actions = detail?.actions ?? []
  const enabled = actions.filter((action) => action.enabled)
  if (enabled.length === 0) return 'No executable quality action exposed.'
  return enabled.map((action) => action.label).join(', ')
}

function urgentReason(check: ObservatoryQualityCheck): string | null {
  const explicit =
    readOptionalMetadata(check, 'last_failure') ??
    readOptionalMetadata(check, 'failure_reason') ??
    readOptionalMetadata(check, 'message')
  if (explicit) return explicit
  if (check.status === 'failing' && check.blocking) {
    return 'This blocking check is failing and should stop promotion.'
  }
  if (check.status === 'warning')
    return 'This check is warning and needs review.'
  if (check.status === 'unknown') return 'No recent evidence has been reported.'
  return null
}

function qualityImpact(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
): string {
  const dataset = qualityDatasetLabel(check, detail)
  if (check.status === 'failing' && check.blocking) {
    return `${dataset} should not be published or promoted until this check passes.`
  }
  if (check.status === 'warning') {
    return `${dataset} needs review before downstream users rely on the latest data.`
  }
  if (check.status === 'unknown') {
    return `${dataset} has no recent evidence, so freshness and trust are uncertain.`
  }
  return `${dataset} currently has passing evidence for this check.`
}

function qualityImpactShort(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
): string {
  const dataset = qualityDatasetLabel(check, detail)
  if (check.status === 'failing' && check.blocking) return `Blocks ${dataset}`
  if (check.status === 'warning') return `Review ${dataset}`
  if (check.status === 'unknown') return `No evidence for ${dataset}`
  return `${dataset} trusted`
}

function qualityEvidenceSummary(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
): string {
  if (!detail) return qualityResultSummary(check)
  const run = detail.history[0]
  const log = detail.logs[0]
  if (run && log) return `${run.status} run · ${log.level} log`
  if (run) return `${run.status} run`
  if (log) return `${log.level} log`
  return 'No evidence linked'
}

function qualityNextActionShort(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
): string {
  const action = (detail?.actions ?? []).find((item) => item.enabled)
  if (action) return action.label
  if (check.status === 'failing') return 'Open run or logs'
  if (check.status === 'unknown') return 'Collect evidence'
  if (check.status === 'warning') return 'Review warning'
  return 'Monitor'
}

function qualityNextAction(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
): string {
  const action = (detail?.actions ?? []).find((item) => item.enabled)
  if (action) return action.label
  if (check.status === 'failing')
    return 'Open the linked run or logs and resolve the failing evidence.'
  if (check.status === 'unknown')
    return 'Run or observe the check so the readiness state has evidence.'
  if (check.status === 'warning')
    return 'Review the latest warning evidence and decide whether it blocks release.'
  return 'Keep monitoring; no immediate action is exposed.'
}

function qualityNextActionReason(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
): string {
  const disabledReason = (detail?.actions ?? []).find(
    (item) => !item.enabled && item.reason,
  )?.reason
  if (disabledReason) return disabledReason
  if (check.status === 'passing') {
    return 'The latest evidence is passing, so this check is not in the attention path.'
  }
  const latestRun = detail?.history[0]
  if (latestRun) {
    return `Start with ${latestRun.name}; it is the run currently attached to this check.`
  }
  const latestLog = detail?.logs[0]
  if (latestLog) {
    return `Start with the ${latestLog.level} log attached to this check.`
  }
  return 'No executable action or linked evidence is available yet.'
}

function readQualityOwner(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
): string {
  const owner =
    readOptionalMetadata(check, 'owner') ??
    (detail?.asset ? readAssetMetadata(detail.asset, 'owner') : null) ??
    (detail?.asset ? readAssetMetadata(detail.asset, 'dataset_owner') : null)
  return owner ?? 'unassigned'
}

interface QualityDatasetTarget {
  id: string
  label: string
}

function qualityDatasetTarget(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
  datasets: Array<ObservatoryDataset>,
): QualityDatasetTarget | null {
  const explicitId =
    readOptionalMetadata(check, 'dataset_id') ??
    readAssetMetadata(detail?.asset, 'dataset_id')
  if (explicitId) {
    const dataset = datasets.find((item) => item.id === explicitId)
    return {
      id: explicitId,
      label: dataset?.name ?? explicitId,
    }
  }

  const sourceIds = new Set(
    [check.asset_id, detail?.asset?.id].filter(
      (value): value is string => typeof value === 'string' && value.length > 0,
    ),
  )
  const matchedById = datasets.find((dataset) => sourceIds.has(dataset.id))
  if (matchedById) {
    return { id: matchedById.id, label: matchedById.name }
  }

  const matchedBySource = datasets.find((dataset) =>
    dataset.source_refs.some((ref) => sourceIds.has(ref.id)),
  )
  if (matchedBySource) {
    return { id: matchedBySource.id, label: matchedBySource.name }
  }

  return null
}

function qualityDatasetLabel(
  check: ObservatoryQualityCheck,
  detail: ObservatoryQualityDetail | null,
): string {
  return (
    readOptionalMetadata(check, 'dataset') ??
    readOptionalMetadata(check, 'dataset_name') ??
    readOptionalMetadata(check, 'dataset_id') ??
    readAssetMetadata(detail?.asset, 'dataset_name') ??
    detail?.asset?.name ??
    `Dataset ${check.asset_id}`
  )
}

function qualityLineageHref(check: ObservatoryQualityCheck): string {
  return `/lineage?assetId=${encodeURIComponent(check.asset_id)}`
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'not timestamped'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function readOptionalMetadata(
  check: ObservatoryQualityCheck,
  key: string,
): string | null {
  const value = check.metadata[key]
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

function readAssetMetadata(
  asset: ObservatoryQualityDetail['asset'] | undefined,
  key: string,
): string | null {
  const value = asset?.metadata[key]
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
