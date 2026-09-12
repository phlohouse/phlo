/**
 * /publishing route. Publication readiness rendered from the canonical
 * verdict phlo-api serves: one bulk readiness request feeds every row, and
 * publish/retire run explain-then-execute against the exact observed state
 * The route computes no eligibility of its own — blocked
 * publishes display the canonical ordered reasons before and after the
 * attempt, and every result reloads durable API state.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Archive, FileText, UploadCloud } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type { DatasetTransitionAction } from '@/observatory/api/datasetProjection'
import type {
  ObservatoryDataset,
  ObservatoryPublishingReadiness,
} from '@/observatory/api/types'
import {
  classifyDatasetTransitionResult,
  datasetTransitionActionId,
} from '@/observatory/api/datasetProjection'
import {
  getObservatoryDatasetRecords,
  getObservatoryPublishingReadinessDirect,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import {
  invalidateCachedResources,
  useLiveResource,
} from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { StatusBadge } from '@/components/observatory/status'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/publishing')({
  component: Publishing,
})

type ReadinessMap = Record<string, ObservatoryPublishingReadiness | undefined>

/** A transition opened for explain; execution happens only after review. */
type PendingTransition = {
  datasetId: string
  action: DatasetTransitionAction
}

export function Publishing() {
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [actionState, setActionState] = useState<'ok' | 'error' | 'unknown'>(
    'unknown',
  )
  const [pending, setPending] = useState<PendingTransition | null>(null)
  const [readinessMap, setReadinessMap] = useState<ReadinessMap>({})
  const [readinessError, setReadinessError] = useState<string | null>(null)
  const [readinessTick, setReadinessTick] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const result = useLiveResource(
    getObservatoryDatasetRecords,
    120_000,
    'observatory:datasets',
  )
  const isLoading = result.data === null && !result.error
  const datasets = result.data ?? []
  const promoted = useMemo(
    () => datasets.filter((dataset) => !dataset.candidate),
    [datasets],
  )
  const published = useMemo(
    () =>
      promoted.filter((dataset) => dataset.publication_state === 'published'),
    [promoted],
  )
  const drafts = useMemo(
    () => promoted.filter((dataset) => dataset.publication_state === 'draft'),
    [promoted],
  )

  // One bulk request serves the canonical verdict for every row; the map is
  // keyed by dataset id and only ever holds server-provided readiness.
  useEffect(() => {
    let cancelled = false
    void getObservatoryPublishingReadinessDirect().then((bulkResult) => {
      if (cancelled) return
      setReadinessMap(
        Object.fromEntries(
          (bulkResult.data ?? []).map((item) => [
            item.dataset_id,
            item.publishing,
          ]),
        ),
      )
      setReadinessError(bulkResult.error)
    })
    return () => {
      cancelled = true
    }
  }, [readinessTick])

  // Durable reload after any transition: bump the readiness walk and
  // invalidate the cached dataset collection, then re-render from the API.
  const reloadDurableState = useCallback(() => {
    invalidateCachedResources([
      'observatory:datasets',
      'observatory:operations',
    ])
    window.dispatchEvent(new Event('focus'))
    setReadinessTick((tick) => tick + 1)
  }, [])

  const selected =
    promoted.find((dataset) => dataset.id === selectedId) ??
    promoted.find(
      (dataset) => (readinessMap[dataset.id]?.blockers.length ?? 0) > 0,
    ) ??
    promoted[0] ??
    null
  const selectDataset = useCallback((datasetId: string) => {
    setSelectedId(datasetId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('datasetId', datasetId)
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'datasetId',
    )
    if (!requested || selectedId === requested) return
    if (promoted.some((dataset) => dataset.id === requested)) {
      setSelectedId(requested)
    }
  }, [promoted, selectedId])

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (!selected || selectedId !== null) return
    const requested = new URLSearchParams(window.location.search).get(
      'datasetId',
    )
    if (requested && promoted.some((dataset) => dataset.id === requested))
      return
    if (requested === selected.id) return
    selectDataset(selected.id)
  }, [promoted, selectDataset, selected, selectedId])

  // Explain-then-execute: the explain panel names the exact dataset, the
  // exact observed version, and the canonical ordered reasons before the
  // confirm button submits the transition. The server stays authoritative.
  const executeTransition = useCallback(
    async (transition: PendingTransition, expectedState: string | null) => {
      const actionId = datasetTransitionActionId(
        transition.datasetId,
        transition.action,
      )
      const next = await runObservatoryActionDirect({
        actionId,
        expectedState,
      })
      const verdict = next.data
        ? classifyDatasetTransitionResult(next.data)
        : null
      setActionMessage(
        verdict?.message ?? next.error ?? 'Transition result unavailable.',
      )
      setActionState(
        verdict?.durable
          ? 'ok'
          : verdict?.outcome === 'blocked'
            ? 'unknown'
            : 'error',
      )
      // Unknown, conflict, and blocked results never become optimistic
      // success: the durable state is reloaded and rendered as-is.
      reloadDurableState()
      return verdict
    },
    [reloadDurableState],
  )

  // Counts come from the canonical verdict only; datasets whose readiness has
  // not loaded count as pending rather than inferred.
  const withReadiness = promoted.filter(
    (dataset) => readinessMap[dataset.id] !== undefined,
  )
  const blockedCount = withReadiness.filter(
    (dataset) => (readinessMap[dataset.id]?.blockers.length ?? 0) > 0,
  ).length
  const needsEvidenceCount = withReadiness.filter(
    (dataset) => (readinessMap[dataset.id]?.missing_evidence.length ?? 0) > 0,
  ).length
  const warningCount = withReadiness.filter(
    (dataset) => (readinessMap[dataset.id]?.warnings.length ?? 0) > 0,
  ).length
  const readinessPending = promoted.length - withReadiness.length

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading ? 'Loading' : `${published.length} published`}
          </Badge>
        }
        description="Review internal publication state, canonical blockers, and explain-then-execute publish or retire transitions."
        title="Publication readiness"
      />
      <StatGrid className="xl:grid-cols-7">
        <StatCard label="Promoted" state="ok" value={promoted.length} />
        <StatCard
          label="Draft"
          state={drafts.length ? 'warning' : 'ok'}
          value={drafts.length}
        />
        <StatCard
          label="Blocked"
          state={blockedCount ? 'error' : 'ok'}
          value={blockedCount}
        />
        <StatCard
          label="Needs evidence"
          state={needsEvidenceCount ? 'unknown' : 'ok'}
          value={needsEvidenceCount}
        />
        <StatCard
          label="Warnings"
          state={warningCount ? 'warning' : 'ok'}
          value={warningCount}
        />
        <StatCard
          label="Readiness pending"
          state={readinessPending ? 'unknown' : 'ok'}
          value={readinessPending}
        />
        <StatCard label="Published" state="ok" value={published.length} />
      </StatGrid>
      <SplitView
        inspector={
          <PublishingInspector
            drafts={drafts.length}
            isLoading={isLoading}
            pending={pending}
            promoted={promoted.length}
            published={published.length}
            readinessMap={readinessMap}
            selected={selected}
            onCancelPending={() => setPending(null)}
            onExecute={executeTransition}
          />
        }
        list={
          <div className="bg-sheet flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b px-3 py-2">
              <UploadCloud className="text-muted-foreground size-3.5" />
              <span className="text-foreground text-xs font-semibold">
                Publication states
              </span>
            </div>
            {result.error ? (
              <EmptyBlock
                className="m-3"
                description={result.error}
                title="No publication state"
              />
            ) : isLoading ? (
              <LoadingBlock
                className="p-3"
                label="Reading Dataset publication readiness from the active lakehouse"
              />
            ) : promoted.length ? (
              <>
                <div className="text-muted-foreground grid grid-cols-[minmax(0,1fr)_7rem_6rem_4rem_10rem] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
                  <span>Dataset</span>
                  <span>Owner</span>
                  <span>Approval</span>
                  <span>Issues</span>
                  <span>Next</span>
                </div>
                <ScrollArea className="min-h-0 flex-1">
                  {promoted.map((dataset) => (
                    <PublishingRow
                      key={dataset.id}
                      dataset={dataset}
                      onExplain={(action) => {
                        selectDataset(dataset.id)
                        setPending({ datasetId: dataset.id, action })
                      }}
                      onSelect={selectDataset}
                      readiness={readinessMap[dataset.id] ?? null}
                      selected={selected?.id === dataset.id}
                    />
                  ))}
                </ScrollArea>
                {readinessError && (
                  <p className="text-status-warning border-t px-3 py-2 font-mono text-[10px]">
                    Canonical readiness unavailable: {readinessError}
                  </p>
                )}
              </>
            ) : (
              <EmptyBlock
                className="m-3"
                description="No promoted Datasets are ready for publication review."
                title="No publication state"
              />
            )}
            {actionMessage && (
              <p
                className={cn(
                  'border-t px-3 py-2 font-mono text-[10px]',
                  actionState === 'ok' && 'text-status-ok',
                  actionState === 'error' && 'text-status-error',
                  actionState === 'unknown' && 'text-muted-foreground',
                )}
              >
                {actionMessage}
              </p>
            )}
          </div>
        }
      />
    </Page>
  )
}

function PublishingRow({
  dataset,
  onExplain,
  onSelect,
  readiness,
  selected,
}: {
  dataset: ObservatoryDataset
  onExplain: (action: DatasetTransitionAction) => void
  onSelect: (datasetId: string) => void
  readiness: ObservatoryPublishingReadiness | null
  selected: boolean
}) {
  // Rows render server facts only: publication state and the canonical
  // verdict read model. No locally inferred blockers or next actions.
  const canonicalIssues = readiness
    ? [
        ...readiness.blockers,
        ...readiness.missing_evidence,
        ...readiness.warnings,
      ]
    : []
  const approval = approvalState(dataset)
  return (
    <div
      className={cn(
        'hover:bg-accent/50 grid w-full cursor-pointer grid-cols-[minmax(0,1fr)_7rem_6rem_4rem_10rem] items-center gap-3 border-b px-3 py-2 transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      data-selected={selected}
      onClick={() => onSelect(dataset.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') onSelect(dataset.id)
      }}
    >
      <div className="flex min-w-0 items-start gap-2">
        <span
          className="status-dot mt-1.5"
          data-state={readiness?.state ?? 'unknown'}
        />
        <div className="min-w-0">
          <Link
            className="text-foreground flex items-center gap-1.5 text-xs font-medium hover:underline"
            params={{ datasetId: dataset.id }}
            to="/datasets/$datasetId"
          >
            <UploadCloud className="text-muted-foreground size-3.5" />
            {dataset.name}
          </Link>
          <div className="text-muted-foreground mt-0.5 font-mono text-[10px]">
            {[
              dataset.owner ? `Owner ${dataset.owner}` : 'No owner',
              `Approval ${approval}`,
              dataset.publication_state,
            ].join(' · ')}
          </div>
          {readiness === null ? (
            <div className="text-muted-foreground mt-0.5 font-mono text-[10px]">
              Canonical readiness loading from phlo-api
            </div>
          ) : (
            canonicalIssues[0] && (
              <div className="text-status-warning mt-0.5 font-mono text-[10px]">
                {canonicalIssues[0]}
              </div>
            )
          )}
        </div>
      </div>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {dataset.owner ?? 'unassigned'}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {approval}
      </span>
      <span className="text-muted-foreground font-mono text-[10px]">
        {readiness ? canonicalIssues.length : '—'}
      </span>
      <div className="flex items-center justify-end gap-2">
        <StatusBadge
          label={dataset.publication_state}
          state={readiness?.state ?? 'unknown'}
        />
        <div className="flex items-center gap-1">
          <Button
            disabled={readiness === null}
            onClick={(event) => {
              event.stopPropagation()
              onExplain('publish')
            }}
            size="xs"
            title="Explain the publish transition before executing"
            type="button"
            variant="outline"
          >
            <UploadCloud className="size-3.5" />
            Publish
          </Button>
          <Button
            disabled={readiness === null}
            onClick={(event) => {
              event.stopPropagation()
              onExplain('retire')
            }}
            size="xs"
            title="Explain the retire transition before executing"
            type="button"
            variant="ghost"
          >
            <Archive className="size-3.5" />
            Retire
          </Button>
        </div>
      </div>
    </div>
  )
}

function approvalState(dataset: ObservatoryDataset): string {
  const explicit = dataset.metadata.approval_state
  return typeof explicit === 'string' && explicit.trim() ? explicit : '—'
}

function PublishingInspector({
  drafts,
  isLoading,
  onCancelPending,
  onExecute,
  pending,
  promoted,
  published,
  readinessMap,
  selected,
}: {
  drafts: number
  isLoading: boolean
  onCancelPending: () => void
  onExecute: (
    transition: PendingTransition,
    expectedState: string | null,
  ) => Promise<{ message: string } | null>
  pending: PendingTransition | null
  promoted: number
  published: number
  readinessMap: ReadinessMap
  selected: ObservatoryDataset | null
}) {
  const [executing, setExecuting] = useState(false)
  if (isLoading) {
    return (
      <InspectorSection label="Policy">
        <LoadingBlock
          className="p-3"
          label="Publication blockers, evidence, and actions will appear once Datasets load"
        />
      </InspectorSection>
    )
  }

  if (!selected) {
    return (
      <InspectorSection label="Policy">
        <p className="text-muted-foreground px-3 pb-3 text-xs">
          No promoted Datasets are ready for publication review.
        </p>
      </InspectorSection>
    )
  }

  const readiness = readinessMap[selected.id] ?? null
  const canonicalIssues = readiness
    ? [
        ...readiness.blockers,
        ...readiness.missing_evidence,
        ...readiness.warnings,
      ]
    : []

  return (
    <>
      <InspectorSection label={`Policy · ${selected.name}`}>
        <p className="text-muted-foreground text-xs/relaxed">
          {selected.description ??
            'Publishing is internal-only here and does not create external sharing.'}
        </p>
        <FactGrid>
          <Fact label="Owner" value={selected.owner ?? 'unassigned'} />
          <Fact
            label="Classification"
            value={selected.classifications.join(', ') || 'unclassified'}
          />
          <Fact label="Promoted" value={promoted} />
          <Fact label="Draft" value={drafts} />
          <Fact label="Published" value={published} />
          <Fact label="Policy" value={readiness?.policy_name ?? 'loading'} />
          <Fact label="Readiness" value={readiness?.state ?? 'loading'} />
        </FactGrid>
      </InspectorSection>
      <InspectorSection label="Release issues">
        <Link
          className="border-input hover:bg-accent mb-2 flex items-center justify-between gap-2 border px-2.5 py-1.5 text-xs font-medium transition-colors"
          params={{ datasetId: selected.id }}
          to="/datasets/$datasetId"
        >
          <span className="flex items-center gap-1.5">
            <FileText className="size-3.5" />
            Open Dataset
          </span>
          <span className="text-muted-foreground font-mono text-[10px]">
            {selected.publication_state}
          </span>
        </Link>
        {readiness === null ? (
          <IssueRow
            detail="no local readiness is assumed"
            state="unknown"
            title="Canonical readiness loading from phlo-api"
          />
        ) : canonicalIssues.length === 0 ? (
          <IssueRow
            detail="readiness verdict is clear"
            state="ok"
            title="No canonical release issues"
          />
        ) : (
          canonicalIssues.map((issue, index) => (
            <IssueRow
              detail={
                readiness.blockers.includes(issue)
                  ? 'blocker'
                  : readiness.missing_evidence.includes(issue)
                    ? 'missing evidence'
                    : 'warning'
              }
              key={`${issue}:${index}`}
              state={
                readiness.blockers.includes(issue)
                  ? 'error'
                  : readiness.missing_evidence.includes(issue)
                    ? 'unknown'
                    : 'warning'
              }
              title={issue}
            />
          ))
        )}
      </InspectorSection>
      {pending && pending.datasetId === selected.id && (
        <InspectorSection label="Pending transition">
          <TransitionExplainPanel
            action={pending.action}
            dataset={selected}
            executing={executing}
            readiness={readiness}
            onCancel={onCancelPending}
            onConfirm={async () => {
              setExecuting(true)
              try {
                await onExecute(pending, selected.publication_state)
              } finally {
                setExecuting(false)
              }
            }}
          />
        </InspectorSection>
      )}
      {!pending && (
        <InspectorSection label="Next">
          <IssueRow
            detail="Publish or Retire on the selected row"
            state="unknown"
            title="Explain a transition to enable it"
          />
        </InspectorSection>
      )}
    </>
  )
}

function IssueRow({
  detail,
  state,
  title,
}: {
  detail: string
  state: 'ok' | 'warning' | 'error' | 'info' | 'unknown'
  title: string
}) {
  return (
    <div className="border-border flex items-start gap-2 border-b py-2 last:border-b-0">
      <span className="status-dot mt-1" data-state={state} />
      <span className="min-w-0">
        <span className="text-foreground block text-[11px]">{title}</span>
        <span className="text-muted-foreground block font-mono text-[10px]">
          {detail}
        </span>
      </span>
    </div>
  )
}

/**
 * Explain panel for one pending transition: exact dataset identity, exact
 * observed compare-and-set version, canonical ordered reasons, and the
 * action's server-provided consequences before the confirm executes it.
 */
function TransitionExplainPanel({
  action,
  dataset,
  executing,
  onConfirm,
  onCancel,
  readiness,
}: {
  action: DatasetTransitionAction
  dataset: ObservatoryDataset
  executing: boolean
  onConfirm: () => void | Promise<void>
  onCancel: () => void
  readiness: ObservatoryPublishingReadiness | null
}) {
  const orderedReasons = readiness
    ? [
        ...readiness.blockers,
        ...readiness.missing_evidence,
        ...readiness.warnings,
      ]
    : []
  const serverAction = readiness?.actions.find((item) => item.id === action)
  return (
    <div className="flex flex-col">
      <IssueRow
        detail="transition runs only after confirmation"
        state="warning"
        title="Explain before execute"
      />
      <IssueRow
        detail={serverAction?.label ?? action}
        state="unknown"
        title="Action"
      />
      <IssueRow detail={dataset.id} state="unknown" title="Dataset" />
      <IssueRow
        detail={dataset.publication_state}
        state="unknown"
        title="Exact version"
      />
      {orderedReasons.map((reason, index) => (
        <IssueRow
          detail="canonical reason"
          key={`${reason}:${index}`}
          state={
            readiness?.blockers.includes(reason)
              ? 'error'
              : readiness?.missing_evidence.includes(reason)
                ? 'unknown'
                : 'warning'
          }
          title={reason}
        />
      ))}
      {(serverAction?.consequences ?? []).map((consequence) => (
        <IssueRow
          detail="consequence"
          key={consequence}
          state="unknown"
          title={consequence}
        />
      ))}
      <div className="flex items-center gap-2 pt-3">
        <Button
          disabled={executing}
          onClick={() => {
            void onConfirm()
          }}
          size="sm"
          type="button"
        >
          {executing ? 'Executing…' : `Confirm ${action}`}
        </Button>
        <Button
          disabled={executing}
          onClick={onCancel}
          size="sm"
          type="button"
          variant="outline"
        >
          Cancel
        </Button>
      </div>
    </div>
  )
}
