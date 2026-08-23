/**
 * /publishing route. Promoted dataset publication queue with bulk readiness
 * checks and publish/archive actions that invalidate cached resources.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Archive, FileText, ShieldAlert, UploadCloud } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ObservatoryDataset,
  ObservatoryHealthState,
  ObservatoryPublishingReadiness,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import {
  getObservatoryDatasetRecords,
  getObservatoryPublishingReadinessDirect,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import { ActionButton } from '@/observatory/components/ActionButton'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { StatusBadge } from '@/observatory/components/StatusBadge'
import {
  invalidateCachedResources,
  useLiveResource,
} from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/publishing')({
  component: Publishing,
})

export function Publishing() {
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [profileResults, setProfileResults] = useState<
    Record<string, ObservatoryResourceResult<ObservatoryPublishingReadiness>>
  >({})
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
  const selected =
    promoted.find((dataset) => dataset.id === selectedId) ??
    promoted.find(
      (dataset) =>
        publicationReadiness(dataset, profileResults[dataset.id]?.data)
          .state !== 'ok',
    ) ??
    promoted[0] ??
    null
  const selectedProfile = selected
    ? (profileResults[selected.id]?.data ?? null)
    : null
  const selectDataset = useCallback((datasetId: string) => {
    setSelectedId(datasetId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('datasetId', datasetId)
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [])

  useEffect(() => {
    let cancelled = false
    if (promoted.length === 0 || Object.keys(profileResults).length > 0) return
    void getObservatoryPublishingReadinessDirect().then((bulkResult) => {
      if (cancelled) return
      setProfileResults(
        Object.fromEntries(
          (bulkResult.data ?? []).map((item) => [
            item.dataset_id,
            { data: item.publishing, error: bulkResult.error },
          ]),
        ),
      )
    })
    return () => {
      cancelled = true
    }
  }, [profileResults, promoted])

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

  return (
    <ObservatoryPage
      kicker="Publishing"
      title="Publication readiness"
      description="Review internal publication state, blockers, and guarded publish or retire actions."
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
                profiles={profileResults}
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
                    onAction={(actionId) => {
                      setActionMessage('Requesting publication action...')
                      void runObservatoryActionDirect({ actionId }).then(
                        (next) => {
                          invalidateCachedResources([
                            'observatory:datasets',
                            'observatory:operations',
                          ])
                          window.dispatchEvent(new Event('focus'))
                          setActionMessage(
                            next.data?.message ??
                              next.error ??
                              'Action requested',
                          )
                        },
                      )
                    }}
                    dataset={dataset}
                    onSelect={selectDataset}
                    profile={profileResults[dataset.id]?.data ?? null}
                    selected={selected?.id === dataset.id}
                  />
                ))}
              </div>
            </>
          ) : (
            <EmptyPublishing detail="No promoted Datasets are ready for publication review." />
          )}
          {actionMessage && (
            <div className="phlo-observatory-panel-footer">{actionMessage}</div>
          )}
        </div>

        <PublishingInspector
          drafts={drafts.length}
          isLoading={isLoading}
          onAction={(actionId) => {
            setActionMessage('Requesting publication action...')
            void runObservatoryActionDirect({ actionId }).then((next) => {
              invalidateCachedResources([
                'observatory:datasets',
                'observatory:operations',
              ])
              window.dispatchEvent(new Event('focus'))
              setActionMessage(
                next.data?.message ?? next.error ?? 'Action requested',
              )
            })
          }}
          profile={selectedProfile}
          promoted={promoted.length}
          published={published.length}
          selected={selected}
        />
      </section>
    </ObservatoryPage>
  )
}

function PublicationSummary({
  datasets,
  drafts,
  profiles,
  promoted,
  published,
}: {
  datasets: Array<ObservatoryDataset>
  drafts: number
  profiles: Record<
    string,
    ObservatoryResourceResult<ObservatoryPublishingReadiness>
  >
  promoted: number
  published: number
}) {
  const readinessList = datasets.map((dataset) =>
    publicationReadiness(dataset, profiles[dataset.id]?.data ?? null),
  )
  const loadedProfiles = Object.values(profiles)
    .map((result) => result.data)
    .filter((profile): profile is ObservatoryPublishingReadiness =>
      Boolean(profile),
    )
  const blocked = readinessList.filter(
    (readiness) => readiness.blockers.length > 0,
  ).length
  const needsEvidence = readinessList.filter(
    (readiness) => readiness.missingEvidence.length > 0,
  ).length
  const warning = readinessList.filter(
    (readiness) => readiness.warnings.length > 0,
  ).length
  const actionReady = loadedProfiles.filter((profile) =>
    profile.actions.some((action) => action.enabled),
  ).length
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
        label="Action ready"
        state={actionReady ? 'ok' : 'unknown'}
        value={actionReady}
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
  onAction,
  onSelect,
  dataset,
  profile,
  selected,
}: {
  onAction: (actionId: string) => void
  onSelect: (datasetId: string) => void
  dataset: ObservatoryDataset
  profile: ObservatoryPublishingReadiness | null
  selected: boolean
}) {
  const publication = publicationReadiness(dataset, profile)
  const approval = approvalState(dataset, publication)
  const nextAction = publicationNextAction(dataset, publication)
  const issueCount = publicationIssueCount(publication)
  const primaryIssue = publicationPrimaryIssue(publication)
  const publishAction = profile?.actions.find(
    (action) => action.id === 'publish',
  )
  const retireAction = profile?.actions.find((action) => action.id === 'retire')

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
      <span className="phlo-observatory-dot" data-state={publication.state} />
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
        {primaryIssue && (
          <div className="phlo-observatory-row-evidence">{primaryIssue}</div>
        )}
      </div>
      <span className="phlo-observatory-publication-cell">
        {dataset.owner ?? 'unassigned'}
      </span>
      <span className="phlo-observatory-publication-cell">{approval}</span>
      <span className="phlo-observatory-publication-cell">{issueCount}</span>
      <div className="phlo-observatory-publication-actions">
        <StatusBadge
          label={dataset.publication_state}
          state={publication.state}
        />
        <span>{nextAction}</span>
        <div className="phlo-observatory-inline-actions">
          <button
            disabled={publishAction ? !publishAction.enabled : issueCount > 0}
            onClick={(event) => {
              event.stopPropagation()
              onAction(`dataset:${dataset.id}:publish`)
            }}
            title={
              publishAction?.reason ??
              (publicationIssues(publication).join(', ') ||
                'Publish internally')
            }
            type="button"
          >
            <UploadCloud className="size-3.5" />
            Publish
          </button>
          <button
            disabled={
              retireAction
                ? !retireAction.enabled
                : dataset.publication_state !== 'published'
            }
            onClick={(event) => {
              event.stopPropagation()
              onAction(`dataset:${dataset.id}:retire`)
            }}
            title={
              retireAction?.reason ?? 'Only published datasets can be retired'
            }
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

type PublicationReadiness = {
  blockers: Array<string>
  missingEvidence: Array<string>
  state: ObservatoryHealthState
  warnings: Array<string>
}

function publicationReadiness(
  dataset: ObservatoryDataset,
  profile: ObservatoryPublishingReadiness | null,
): PublicationReadiness {
  if (profile) {
    return {
      blockers: profile.blockers,
      missingEvidence: profile.missing_evidence,
      state: profile.state,
      warnings: profile.warnings,
    }
  }
  const blockers: Array<string> = []
  const missingEvidence: Array<string> = []
  const warnings: Array<string> = []
  if (!dataset.owner) blockers.push('owner missing')
  if (dataset.classifications.length === 0)
    blockers.push('classification missing')
  if (dataset.readiness_state === 'error') blockers.push('quality blocking')
  if (dataset.readiness_state === 'unknown')
    missingEvidence.push('readiness evidence missing')
  if (dataset.readiness_state === 'warning')
    warnings.push('readiness warning requires review')
  return { blockers, missingEvidence, state: dataset.readiness_state, warnings }
}

function publicationIssues(readiness: PublicationReadiness): Array<string> {
  return [
    ...readiness.blockers,
    ...readiness.missingEvidence,
    ...readiness.warnings,
  ]
}

function publicationIssueCount(readiness: PublicationReadiness): number {
  return publicationIssues(readiness).length
}

function publicationPrimaryIssue(
  readiness: PublicationReadiness,
): string | null {
  return publicationIssues(readiness)[0] ?? null
}

function approvalState(
  dataset: ObservatoryDataset,
  readiness: PublicationReadiness,
): string {
  const explicit = dataset.metadata.approval_state
  if (typeof explicit === 'string' && explicit.trim()) return explicit
  if (dataset.publication_state === 'published') return 'approved'
  if (readiness.blockers.length > 0) return 'blocked'
  if (readiness.missingEvidence.length > 0) return 'needs evidence'
  if (readiness.warnings.length > 0) return 'review'
  return 'ready'
}

function publicationNextAction(
  dataset: ObservatoryDataset,
  readiness: PublicationReadiness,
): string {
  if (dataset.publication_state === 'published') return 'Retire if obsolete'
  if (readiness.blockers.length > 0) return 'Resolve blockers'
  if (readiness.missingEvidence.length > 0) return 'Collect evidence'
  if (readiness.warnings.length > 0) return 'Review warning'
  return 'Publish internally'
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
  onAction,
  profile,
  promoted,
  published,
  selected,
}: {
  drafts: number
  isLoading: boolean
  onAction: (actionId: string) => void
  profile: ObservatoryPublishingReadiness | null
  promoted: number
  published: number
  selected: ObservatoryDataset | null
}) {
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
  const publishing = profile
  const readiness = publicationReadiness(selected, profile)
  const nextAction = publishing
    ? (publishing.actions.find((action) => action.enabled)?.label ??
      publishing.actions[0]?.reason ??
      publicationNextAction(selected, readiness))
    : publicationNextAction(selected, readiness)
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
        <dd>{publishing?.policy_name ?? 'loading'}</dd>
        <dt>Readiness</dt>
        <dd>{publishing?.state ?? selected.readiness_state}</dd>
        <dt>Next action</dt>
        <dd>{nextAction}</dd>
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
        {readiness.blockers.map((blocker) => (
          <div
            className="phlo-observatory-mini-row"
            data-state="error"
            key={blocker}
          >
            <span>{blocker}</span>
            <small>blocker</small>
          </div>
        ))}
        {readiness.missingEvidence.map((item) => (
          <div
            className="phlo-observatory-mini-row"
            data-state="unknown"
            key={item}
          >
            <span>{item}</span>
            <small>missing evidence</small>
          </div>
        ))}
        {readiness.warnings.map((warning) => (
          <div
            className="phlo-observatory-mini-row"
            data-state="warning"
            key={warning}
          >
            <span>{warning}</span>
            <small>warning</small>
          </div>
        ))}
        {readiness.blockers.length === 0 &&
          readiness.missingEvidence.length === 0 &&
          readiness.warnings.length === 0 && (
            <div className="phlo-observatory-mini-row" data-state="ok">
              <span>No release issues</span>
              <small>ready for publication action</small>
            </div>
          )}
      </div>
      {publishing && (
        <>
          <div className="phlo-observatory-action-row">
            {publishing.actions.map((action) => (
              <ActionButton
                action={{
                  ...action,
                  id: `dataset:${selected.id}:${action.id}`,
                  kind: `dataset.${action.id}`,
                  requires_confirmation: true,
                  risk_level: action.id === 'retire' ? 'medium' : 'low',
                  expected_evidence: [],
                }}
                key={action.id}
                onRun={onAction}
              />
            ))}
          </div>
          <div className="phlo-observatory-detail-list">
            {publishing.actions
              .filter((action) => !action.enabled && action.reason)
              .map((action) => (
                <div
                  className="phlo-observatory-mini-row"
                  data-state="unknown"
                  key={action.id}
                >
                  <span>{action.label} unavailable</span>
                  <small>{action.reason}</small>
                </div>
              ))}
          </div>
        </>
      )}
    </aside>
  )
}
