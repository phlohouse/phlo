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
import {
  invalidateCachedResources,
  loadCachedResource,
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
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

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
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading ? 'Loading' : `${checks.length} checks`}
          </Badge>
        }
        description="Resolve the exact checks blocking trust, publication, and downstream use."
        title="Quality triage"
      />
      <SplitView
        inspector={
          <>
            <InspectorSection
              label={`Triage evidence · ${selected?.name ?? 'none'}`}
            >
              {selected ? (
                <>
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-muted-foreground min-w-0 text-xs/relaxed">
                      {selected.description ??
                        `Dataset ${qualityDatasetLabel(selected, selectedDetail)}`}
                    </p>
                    <Badge variant="secondary">
                      {qualityStatusLabel(selected)}
                    </Badge>
                  </div>
                  <FactGrid>
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
                  </FactGrid>
                  <div className="border-border flex flex-col gap-0.5 border-l-2 px-3 py-1.5">
                    <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
                      Next action
                    </span>
                    <strong className="text-foreground text-[11px]">
                      {qualityNextAction(selected, selectedDetail)}
                    </strong>
                    <span className="text-muted-foreground text-[10px]/relaxed">
                      {qualityNextActionReason(selected, selectedDetail)}
                    </span>
                  </div>
                </>
              ) : (
                <p className="text-muted-foreground text-xs">
                  {isLoading
                    ? 'Loading selected quality check and triage evidence.'
                    : 'No quality check selected.'}
                </p>
              )}
            </InspectorSection>
            {selected && (
              <>
                <InspectorSection label="Dataset readiness">
                  <DatasetReadinessContext
                    profile={selectedDatasetProfile}
                    selected={selected}
                    target={selectedDatasetTarget}
                  />
                </InspectorSection>
                <InspectorSection label="Evidence">
                  <QualityEvidence
                    detail={selectedDetail}
                    selected={selected}
                    target={selectedDatasetTarget}
                  />
                </InspectorSection>
                <InspectorSection label="History">
                  <QualityHistory detail={selectedDetail} selected={selected} />
                </InspectorSection>
                <InspectorSection label="Next actions">
                  <QualityNextActions
                    detail={selectedDetail}
                    selected={selected}
                    target={selectedDatasetTarget}
                  />
                  {(selectedDetail?.actions ?? []).length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5 pt-2">
                      {(selectedDetail?.actions ?? []).map((action) => (
                        <ActionButton
                          action={action}
                          key={action.id}
                          onRun={(actionId) => {
                            void runObservatoryAction({
                              data: { actionId },
                            }).then((next) => {
                              invalidateCachedResources([
                                'observatory:operations',
                                'observatory:quality',
                              ])
                              setActionMessage(
                                next.data?.message ??
                                  next.error ??
                                  'Action requested',
                              )
                            })
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
                </InspectorSection>
              </>
            )}
            {(detail.error ?? datasetProfile.error ?? result.error) && (
              <InspectorSection label="Errors">
                {detail.error && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    {detail.error}
                  </p>
                )}
                {datasetProfile.error && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    Dataset readiness context is unavailable:{' '}
                    {datasetProfile.error}
                  </p>
                )}
                {result.error && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    {result.error}
                  </p>
                )}
              </InspectorSection>
            )}
          </>
        }
        inspectorWidth="w-[24rem]"
        list={
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
            {selected && (
              <SelectedQualityWorkbench
                detail={selectedDetail}
                selected={selected}
                target={selectedDatasetTarget}
              />
            )}
            <div className="flex items-end gap-4">
              <div className="flex flex-col">
                <strong className="text-foreground text-3xl font-semibold tracking-tight">
                  {isLoading ? '—' : (score ?? '—')}
                </strong>
                <span className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
                  Observed health
                </span>
                <span className="text-muted-foreground font-mono text-[10px]">
                  {isLoading
                    ? 'Reading live quality evidence'
                    : `${observed} observed · ${failing} failing · ${unknown} pending`}
                </span>
              </div>
              <StatGrid className="flex-1 xl:grid-cols-3">
                <StatCard
                  icon={<Shield className="size-3.5" />}
                  label="Blocking"
                  state={blocking ? 'error' : 'ok'}
                  value={isLoading ? '—' : blocking}
                />
                <StatCard
                  icon={<AlertTriangle className="size-3.5" />}
                  label="Warnings"
                  state={warnings ? 'warning' : 'ok'}
                  value={isLoading ? '—' : warnings}
                />
                <StatCard
                  icon={<CircleHelp className="size-3.5" />}
                  label="Not observed"
                  value={isLoading ? '—' : unknown}
                />
              </StatGrid>
            </div>
            <div
              aria-label="Quality views"
              className="flex items-center gap-1.5"
              role="tablist"
            >
              {qualityViews.map((view) => (
                <Button
                  aria-selected={activeView === view.id}
                  key={view.id}
                  onClick={() => setActiveView(view.id)}
                  role="tab"
                  size="xs"
                  type="button"
                  variant={activeView === view.id ? 'default' : 'outline'}
                >
                  {view.icon}
                  {view.label}
                </Button>
              ))}
            </div>
            {activeView === 'graph' ? (
              <SectionCard
                actions={
                  <Badge variant="secondary">
                    {isLoading ? 'Loading' : `${graph.edges.length} bindings`}
                  </Badge>
                }
                contentClassName="p-0"
                title="Quality dependencies"
              >
                <div className="bg-surface-sunken min-h-[26rem]">
                  <ObservatoryFlowCanvas
                    edges={graph.edges}
                    nodes={graph.nodes}
                    onSelect={selectCheck}
                    selectedId={selected?.id}
                  />
                </div>
              </SectionCard>
            ) : (
              <SectionCard contentClassName="p-0" title="Check queue">
                <div className="border-border text-muted-foreground grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 border-b px-3 py-1.5 text-[9px] font-medium tracking-widest uppercase">
                  <span>Check</span>
                  <span>Impact</span>
                  <span>Evidence</span>
                  <span>Next</span>
                </div>
                <ScrollArea className="max-h-[30rem]">
                  <div className="divide-border divide-y">
                    {sortedChecks.map((check) => (
                      <CheckRow
                        key={check.id}
                        check={check}
                        detail={
                          check.id === selected?.id ? selectedDetail : null
                        }
                        onSelect={selectCheck}
                        selected={check.id === selected?.id}
                      />
                    ))}
                    {isLoading ? (
                      <LoadingBlock
                        className="p-3"
                        label="Reading live quality checks and evidence"
                      />
                    ) : (
                      checks.length === 0 && (
                        <EmptyBlock
                          description="Register quality checks in the lakehouse to see them here."
                          title="No quality checks registered yet."
                        />
                      )
                    )}
                  </div>
                </ScrollArea>
              </SectionCard>
            )}
          </div>
        }
      />
    </Page>
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
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
          Dataset readiness
        </span>
        {target ? (
          <Link
            className="text-foreground text-[10px] font-medium hover:underline"
            params={{ datasetId: target.id }}
            to="/datasets/$datasetId"
          >
            Open Dataset
          </Link>
        ) : (
          <Link
            className="text-foreground text-[10px] font-medium hover:underline"
            to={qualityLineageHref(selected)}
          >
            Open Lineage
          </Link>
        )}
      </div>
      {profile && dataset ? (
        <>
          <FactGrid className="grid-cols-2">
            <Fact label="Publication" value={dataset.publication_state} />
            <Fact label="Readiness" value={dataset.readiness_state} />
            <Fact label="Owner" value={dataset.owner ?? 'unassigned'} />
            <Fact
              label="Classifications"
              value={
                dataset.classifications.length
                  ? dataset.classifications.join(', ')
                  : 'unassigned'
              }
            />
          </FactGrid>
          <div className="divide-border -mx-3 divide-y border-y">
            {blockers.slice(0, 3).map((blocker) => (
              <MiniRow detail="release blocker" key={blocker} label={blocker} />
            ))}
            {missingEvidence
              .slice(0, 3 - blockers.slice(0, 3).length)
              .map((item) => (
                <MiniRow
                  detail="missing evidence"
                  key={item}
                  label={item}
                  state="unknown"
                />
              ))}
            {warnings
              .slice(
                0,
                Math.max(0, 3 - blockers.length - missingEvidence.length),
              )
              .map((warning) => (
                <MiniRow
                  detail="warning"
                  key={warning}
                  label={warning}
                  state="warning"
                />
              ))}
            {blockers.length === 0 &&
              missingEvidence.length === 0 &&
              warnings.length === 0 &&
              failingControls.length === 0 && (
                <MiniRow
                  detail={profile.publishing.policy_name}
                  label="Ready for publication controls"
                />
              )}
            {failingControls
              .filter((control) => !blockers.includes(control.message ?? ''))
              .slice(0, 2)
              .map((control) => (
                <MiniRow
                  detail={control.message ?? control.status}
                  key={control.id}
                  label={control.label}
                />
              ))}
          </div>
        </>
      ) : (
        <div className="divide-border -mx-3 divide-y border-y">
          <MiniRow
            detail={
              target?.label ??
              'Use lineage evidence to bind this resource to a Dataset.'
            }
            label={
              target ? 'Loading readiness context' : 'No Dataset binding found'
            }
          />
        </div>
      )}
    </div>
  )
}

function MiniRow({
  detail,
  label,
  state,
}: {
  detail: string
  label: string
  state?: string
}) {
  return (
    <div
      className="flex items-center justify-between gap-2 px-3 py-2"
      data-state={state}
    >
      <span className="text-foreground min-w-0 text-[11px]">{label}</span>
      <span className="text-muted-foreground flex-none text-right font-mono text-[10px]">
        {detail}
      </span>
    </div>
  )
}

function EvidenceCard({
  children,
  href,
  label,
  title,
}: {
  children: ReactNode
  href?: string
  label: string
  title: ReactNode
}) {
  const content = (
    <>
      <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
        {label}
      </span>
      <span className="text-foreground text-[11px] font-medium break-all">
        {title}
      </span>
      <span className="text-muted-foreground text-[10px]/relaxed">
        {children}
      </span>
    </>
  )
  const className =
    'border-border hover:bg-accent/50 flex flex-col gap-0.5 border-l-2 px-3 py-1.5 transition-colors'
  if (href) {
    return (
      <a className={className} href={href}>
        {content}
      </a>
    )
  }
  return <div className={className}>{content}</div>
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
    <div className="flex flex-col gap-2">
      <EvidenceCard label="Impact" title={qualityImpact(selected, detail)}>
        {urgentReason(selected) ?? qualityResultSummary(selected)}
      </EvidenceCard>
      <EvidenceCard
        href={
          latestRun
            ? `/operations?operationId=${encodeURIComponent(latestRun.id)}`
            : undefined
        }
        label="Related run"
        title={latestRun?.name ?? 'No linked run'}
      >
        {latestRun
          ? `${latestRun.status} · ${formatDateTime(latestRun.completed_at)}`
          : 'No run evidence is linked to this check yet.'}
      </EvidenceCard>
      <EvidenceCard
        href={
          latestLog
            ? `/logs?logId=${encodeURIComponent(latestLog.id)}`
            : undefined
        }
        label="Latest log"
        title={latestLog?.message ?? 'No linked log'}
      >
        {latestLog
          ? `${latestLog.level} · ${formatDateTime(latestLog.timestamp)}`
          : 'No log evidence is linked to this check yet.'}
      </EvidenceCard>
      <EvidenceCard
        label="Evidence depth"
        title={`${history.length} runs · ${logs.length} logs`}
      >
        {qualityActionsSummary(detail)}
      </EvidenceCard>
      {target ? (
        <Link
          className="border-border hover:bg-accent/50 flex flex-col gap-0.5 border-l-2 px-3 py-1.5 transition-colors"
          params={{ datasetId: target.id }}
          to="/datasets/$datasetId"
        >
          <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
            Dataset profile
          </span>
          <span className="text-foreground text-[11px] font-medium">
            {target.label}
          </span>
          <span className="text-muted-foreground text-[10px]/relaxed">
            Readiness, ownership, publication, and controls.
          </span>
        </Link>
      ) : (
        <a
          className="border-border hover:bg-accent/50 flex flex-col gap-0.5 border-l-2 px-3 py-1.5 transition-colors"
          href={qualityLineageHref(selected)}
        >
          <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
            Source binding
          </span>
          <span className="text-foreground text-[11px] font-medium break-all">
            {detail?.asset?.name ?? selected.asset_id}
          </span>
          <span className="text-muted-foreground text-[10px]/relaxed">
            Bind this source before treating it as a Dataset.
          </span>
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
    <div className="divide-border -mx-3 divide-y border-y">
      {history.slice(0, 3).map((run) => (
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
          key={run.id}
          search={{ operationId: run.id }}
          to="/operations"
        >
          <span className="text-foreground min-w-0 truncate text-[11px]">
            {run.name}
          </span>
          <span className="text-muted-foreground flex-none font-mono text-[10px]">
            {run.status} · {formatDateTime(run.completed_at)}
          </span>
        </Link>
      ))}
      {logs.slice(0, Math.max(0, 3 - history.length)).map((log) => (
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
          key={log.id}
          search={{ logId: log.id }}
          to="/logs"
        >
          <span className="text-foreground min-w-0 truncate text-[11px]">
            {log.message}
          </span>
          <span className="text-muted-foreground flex-none font-mono text-[10px]">
            {log.level} · {formatDateTime(log.timestamp)}
          </span>
        </Link>
      ))}
      {history.length === 0 && logs.length === 0 && (
        <MiniRow
          detail={selected.asset_id}
          label="No attached history yet"
          state="unknown"
        />
      )}
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
    <div className="divide-border -mx-3 divide-y border-y">
      {enabledAction ? (
        <MiniRow
          detail={[
            'available now',
            enabledAction.risk_level
              ? `${enabledAction.risk_level} risk`
              : null,
          ]
            .filter(Boolean)
            .join(' · ')}
          label={enabledAction.label}
          state="ok"
        />
      ) : (
        <MiniRow
          detail={disabledReason ?? qualityNextActionReason(selected, detail)}
          label={qualityNextActionShort(selected, detail)}
          state="unknown"
        />
      )}
      {latestRun && (
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
          search={{ operationId: latestRun.id }}
          to="/operations"
        >
          <span className="text-foreground text-[11px]">Open related run</span>
          <span className="text-muted-foreground flex-none font-mono text-[10px]">
            {latestRun.name}
          </span>
        </Link>
      )}
      {latestLog && (
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
          search={{ logId: latestLog.id }}
          to="/logs"
        >
          <span className="text-foreground text-[11px]">Open latest log</span>
          <span className="text-muted-foreground flex-none font-mono text-[10px]">
            {latestLog.level}
          </span>
        </Link>
      )}
      {target ? (
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
          params={{ datasetId: target.id }}
          to="/datasets/$datasetId"
        >
          <span className="text-foreground text-[11px]">
            Open affected Dataset
          </span>
          <span className="text-muted-foreground flex-none font-mono text-[10px]">
            {target.label}
          </span>
        </Link>
      ) : (
        <Link
          className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
          to={qualityLineageHref(selected)}
        >
          <span className="text-foreground text-[11px]">
            Open lineage binding
          </span>
          <span className="text-muted-foreground flex-none font-mono text-[10px]">
            {selected.asset_id}
          </span>
        </Link>
      )}
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
    <section
      className="bg-card ring-foreground/10 ring-1"
      data-state={qualityVisualState(selected)}
    >
      <div className="border-border flex flex-wrap items-start justify-between gap-3 border-b px-3 py-3">
        <div className="min-w-0">
          <span className="text-muted-foreground flex items-center gap-1.5 text-[9px] font-medium tracking-widest uppercase">
            <span
              className="status-dot"
              data-state={qualityVisualState(selected)}
            />
            {qualityStatusLabel(selected)}
          </span>
          <h2 className="text-foreground mt-0.5 text-base font-semibold">
            {selected.name}
          </h2>
          <p className="text-muted-foreground mt-0.5 text-xs/relaxed">
            {qualityImpact(selected, detail)}
          </p>
        </div>
        <Button
          nativeButton={false}
          render={<a href={resourceHref} />}
          size="xs"
          variant="outline"
        >
          <Database className="size-3.5" />
          {target?.label ?? datasetLabel}
        </Button>
      </div>
      <div className="grid grid-cols-4 max-xl:grid-cols-2">
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
    </section>
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
      <span className="text-muted-foreground flex items-center gap-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
        {icon}
        {label}
      </span>
      <strong className="text-foreground mt-1 text-[11px] break-all">
        {title}
      </strong>
      <span className="text-muted-foreground mt-0.5 line-clamp-2 text-[10px]/relaxed">
        {detail}
      </span>
      {href && (
        <ExternalLink className="text-muted-foreground absolute top-2 right-2 size-3" />
      )}
    </>
  )
  const className = cn(
    'border-border relative flex flex-col border-r px-3 py-2.5 text-left',
    href && 'hover:bg-accent/50 transition-colors',
  )

  if (href) {
    return (
      <a className={className} href={href}>
        {content}
      </a>
    )
  }

  return <div className={className}>{content}</div>
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
      className={cn(
        'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] items-start gap-2 px-3 py-2 text-left transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      data-active={selected}
      onClick={() => onSelect(check.id)}
      type="button"
    >
      <span className="flex min-w-0 items-start gap-2">
        <span
          className="status-dot mt-1 flex-none"
          data-state={
            check.status === 'failing'
              ? 'error'
              : check.status === 'unknown'
                ? 'warning'
                : check.status
          }
        />
        <span className="min-w-0">
          <span className="text-foreground flex items-center gap-1.5 text-xs font-medium">
            <ShieldCheck className="text-muted-foreground size-3.5 flex-none" />
            <span className="truncate">{check.name}</span>
          </span>
          <span className="text-muted-foreground mt-0.5 block truncate font-mono text-[10px]">
            {qualityDatasetLabel(check, detail)} · {qualityStatusLabel(check)} ·{' '}
            {check.severity ?? 'severity unset'} ·{' '}
            {check.blocking ? 'blocking' : 'advisory'}
          </span>
        </span>
      </span>
      <span className="text-muted-foreground truncate text-[10px]/relaxed">
        {qualityImpactShort(check, detail)}
      </span>
      <span className="text-muted-foreground truncate text-[10px]/relaxed">
        {qualityEvidenceSummary(check, detail)}
      </span>
      <span className="text-muted-foreground truncate text-[10px]/relaxed">
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
