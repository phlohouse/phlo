/**
 * /publishing route. Publication readiness rendered from the canonical
 * verdict phlo-api serves: one bulk readiness request feeds every row, and
 * publish/retire run explain-then-execute against the exact observed state
 * The route computes no eligibility of its own — blocked
 * publishes display the canonical ordered reasons before and after the
 * attempt, and every result reloads durable API state.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Archive, FileText, ShieldAlert, UploadCloud } from 'lucide-react'
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
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { StatusBadge } from '@/observatory/components/StatusBadge'
import {
  invalidateCachedResources,
  useLiveResource,
} from '@/observatory/routes/liveResource'

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

  return (
    <ObservatoryPage
      kicker="Publishing"
      title="Publication readiness"
      description="Review internal publication state, canonical blockers, and explain-then-execute publish or retire transitions."
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${published.length} published`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-browser-toolbar">
            <div className="phlo-observatory-row-title">
              <UploadCloud className="size-4" />
              Publication states
            </div>
          </div>
          {result.error ? (
            <EmptyPublishing detail={result.error} />
          ) : isLoading ? (
            <EmptyPublishing detail="Reading Dataset publication readiness from the active lakehouse." />
          ) : promoted.length ? (
            <>
              <PublicationSummary
                drafts={drafts.length}
                datasets={promoted}
                promoted={promoted.length}
                published={published.length}
                readinessMap={readinessMap}
              />
              <div className="phlo-observatory-publication-head">
                <span>Dataset</span>
                <span>Owner</span>
                <span>Approval</span>
                <span>Issues</span>
                <span>Next</span>
              </div>
              <div className="phlo-observatory-list">
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
              </div>
              {readinessError && (
                <div className="phlo-observatory-panel-footer">
                  Canonical readiness unavailable: {readinessError}
                </div>
              )}
            </>
          ) : (
            <EmptyPublishing detail="No promoted Datasets are ready for publication review." />
          )}
          {actionMessage && (
            <div
              className="phlo-observatory-panel-footer"
              data-state={actionState}
            >
              {actionMessage}
            </div>
          )}
        </div>

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
      </section>
    </ObservatoryPage>
  )
}

function PublicationSummary({
  drafts,
  promoted,
  published,
  readinessMap,
  datasets,
}: {
  datasets: Array<ObservatoryDataset>
  drafts: number
  promoted: number
  published: number
  readinessMap: ReadinessMap
}) {
  // Counts come from the canonical verdict only; datasets whose readiness has
  // not loaded count as pending rather than inferred.
  const withReadiness = datasets.filter(
    (dataset) => readinessMap[dataset.id] !== undefined,
  )
  const blocked = withReadiness.filter(
    (dataset) => (readinessMap[dataset.id]?.blockers.length ?? 0) > 0,
  ).length
  const needsEvidence = withReadiness.filter(
    (dataset) => (readinessMap[dataset.id]?.missing_evidence.length ?? 0) > 0,
  ).length
  const warning = withReadiness.filter(
    (dataset) => (readinessMap[dataset.id]?.warnings.length ?? 0) > 0,
  ).length
  const pendingCount = datasets.length - withReadiness.length
  return (
    <div className="phlo-observatory-publication-summary">
      <PublicationSummaryCell label="Promoted" state="ok" value={promoted} />
      <PublicationSummaryCell
        label="Draft"
        state={drafts ? 'warning' : 'ok'}
        value={drafts}
      />
      <PublicationSummaryCell
        label="Blocked"
        state={blocked ? 'error' : 'ok'}
        value={blocked}
      />
      <PublicationSummaryCell
        label="Needs evidence"
        state={needsEvidence ? 'unknown' : 'ok'}
        value={needsEvidence}
      />
      <PublicationSummaryCell
        label="Warnings"
        state={warning ? 'warning' : 'ok'}
        value={warning}
      />
      <PublicationSummaryCell
        label="Readiness pending"
        state={pendingCount ? 'unknown' : 'ok'}
        value={pendingCount}
      />
      <PublicationSummaryCell label="Published" state="ok" value={published} />
    </div>
  )
}

function PublicationSummaryCell({
  label,
  state,
  value,
}: {
  label: string
  state: string
  value: string | number
}) {
  return (
    <div
      className="phlo-observatory-publication-summary-cell"
      data-state={state}
    >
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
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
      className="phlo-observatory-dataset-row phlo-observatory-publication-row"
      data-selected={selected}
      onClick={() => onSelect(dataset.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') onSelect(dataset.id)
      }}
    >
      <span
        className="phlo-observatory-dot"
        data-state={readiness?.state ?? 'unknown'}
      />
      <div>
        <Link
          className="phlo-observatory-row-title"
          params={{ datasetId: dataset.id }}
          to="/datasets/$datasetId"
        >
          <UploadCloud className="size-4" />
          {dataset.name}
        </Link>
        <div className="phlo-observatory-row-meta">
          {[
            dataset.owner ? `Owner ${dataset.owner}` : 'No owner',
            `Approval ${approval}`,
            dataset.publication_state,
          ].join(' · ')}
        </div>
        {readiness === null ? (
          <div className="phlo-observatory-row-evidence">
            Canonical readiness loading from phlo-api
          </div>
        ) : (
          canonicalIssues[0] && (
            <div className="phlo-observatory-row-evidence">
              {canonicalIssues[0]}
            </div>
          )
        )}
      </div>
      <span className="phlo-observatory-publication-cell">
        {dataset.owner ?? 'unassigned'}
      </span>
      <span className="phlo-observatory-publication-cell">{approval}</span>
      <span className="phlo-observatory-publication-cell">
        {readiness ? canonicalIssues.length : '—'}
      </span>
      <div className="phlo-observatory-publication-actions">
        <StatusBadge
          label={dataset.publication_state}
          state={readiness?.state ?? 'unknown'}
        />
        <div className="phlo-observatory-inline-actions">
          <button
            disabled={readiness === null}
            onClick={(event) => {
              event.stopPropagation()
              onExplain('publish')
            }}
            title="Explain the publish transition before executing"
            type="button"
          >
            <UploadCloud className="size-3.5" />
            Publish
          </button>
          <button
            disabled={readiness === null}
            onClick={(event) => {
              event.stopPropagation()
              onExplain('retire')
            }}
            title="Explain the retire transition before executing"
            type="button"
          >
            <Archive className="size-3.5" />
            Retire
          </button>
        </div>
      </div>
    </div>
  )
}

function approvalState(dataset: ObservatoryDataset): string {
  const explicit = dataset.metadata.approval_state
  return typeof explicit === 'string' && explicit.trim() ? explicit : '—'
}

function EmptyPublishing({ detail }: { detail: string }) {
  return (
    <div className="phlo-observatory-operation-empty">
      <div>
        <span className="phlo-observatory-inspector-label">Publishing</span>
        <h2>No publication state</h2>
        <p>
          <ShieldAlert className="size-4" />
          {detail}
        </p>
      </div>
    </div>
  )
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
      <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
        <div className="phlo-observatory-inspector-label">Policy</div>
        <h2>Loading readiness</h2>
        <p>
          Publication blockers, evidence, and actions will appear once Datasets
          load.
        </p>
      </aside>
    )
  }

  if (!selected) {
    return (
      <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
        <div className="phlo-observatory-inspector-label">Policy</div>
        <h2>Internal publication</h2>
        <p>No promoted Datasets are ready for publication review.</p>
      </aside>
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
    <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
      <div className="phlo-observatory-inspector-label">Policy</div>
      <h2>{selected.name}</h2>
      <p>
        {selected.description ??
          'Publishing is internal-only here and does not create external sharing.'}
      </p>
      <dl className="phlo-observatory-facts">
        <dt>Owner</dt>
        <dd>{selected.owner ?? 'unassigned'}</dd>
        <dt>Classification</dt>
        <dd>{selected.classifications.join(', ') || 'unclassified'}</dd>
        <dt>Promoted</dt>
        <dd>{promoted}</dd>
        <dt>Draft</dt>
        <dd>{drafts}</dd>
        <dt>Published</dt>
        <dd>{published}</dd>
        <dt>Policy</dt>
        <dd>{readiness?.policy_name ?? 'loading'}</dd>
        <dt>Readiness</dt>
        <dd>{readiness?.state ?? 'loading'}</dd>
      </dl>
      <div className="phlo-observatory-detail-list">
        <Link
          className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
          params={{ datasetId: selected.id }}
          to="/datasets/$datasetId"
        >
          <span>
            <FileText className="size-3.5" />
            Open Dataset
          </span>
          <small>{selected.publication_state}</small>
        </Link>
        {readiness === null ? (
          <div className="phlo-observatory-mini-row" data-state="unknown">
            <span>Canonical readiness loading from phlo-api</span>
            <small>no local readiness is assumed</small>
          </div>
        ) : canonicalIssues.length === 0 ? (
          <div className="phlo-observatory-mini-row" data-state="ok">
            <span>No canonical release issues</span>
            <small>readiness verdict is clear</small>
          </div>
        ) : (
          canonicalIssues.map((issue, index) => (
            <div
              className="phlo-observatory-mini-row"
              data-state={
                readiness.blockers.includes(issue)
                  ? 'error'
                  : readiness.missing_evidence.includes(issue)
                    ? 'unknown'
                    : 'warning'
              }
              key={`${issue}:${index}`}
            >
              <span>{issue}</span>
              <small>
                {readiness.blockers.includes(issue)
                  ? 'blocker'
                  : readiness.missing_evidence.includes(issue)
                    ? 'missing evidence'
                    : 'warning'}
              </small>
            </div>
          ))
        )}
      </div>
      {pending && pending.datasetId === selected.id && (
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
      )}
      {!pending && (
        <div className="phlo-observatory-detail-list">
          <div className="phlo-observatory-mini-row" data-state="unknown">
            <span>Explain a transition to enable it</span>
            <small>Publish or Retire on the selected row</small>
          </div>
        </div>
      )}
    </aside>
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
    <div className="phlo-observatory-detail-list" data-state="warning">
      <div className="phlo-observatory-mini-row">
        <span>Explain before execute</span>
        <small>transition runs only after confirmation</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Action</span>
        <small>{serverAction?.label ?? action}</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Dataset</span>
        <small>{dataset.id}</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Exact version</span>
        <small>{dataset.publication_state}</small>
      </div>
      {orderedReasons.map((reason, index) => (
        <div
          className="phlo-observatory-mini-row"
          data-state={
            readiness?.blockers.includes(reason)
              ? 'error'
              : readiness?.missing_evidence.includes(reason)
                ? 'unknown'
                : 'warning'
          }
          key={`${reason}:${index}`}
        >
          <span>{reason}</span>
          <small>canonical reason</small>
        </div>
      ))}
      {(serverAction?.consequences ?? []).map((consequence) => (
        <div className="phlo-observatory-mini-row" key={consequence}>
          <span>{consequence}</span>
          <small>consequence</small>
        </div>
      ))}
      <div className="phlo-observatory-inline-actions">
        <button
          disabled={executing}
          onClick={() => {
            void onConfirm()
          }}
          type="button"
        >
          {executing ? 'Executing…' : `Confirm ${action}`}
        </button>
        <button disabled={executing} onClick={onCancel} type="button">
          Cancel
        </button>
      </div>
    </div>
  )
}
