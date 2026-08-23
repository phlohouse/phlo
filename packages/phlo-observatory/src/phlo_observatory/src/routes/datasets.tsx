/**
 * /datasets route. Parent layout listing promoted datasets and candidates
 * with owner, classification, publication, and readiness filters; the
 * selected dataset's child route renders in the outlet.
 */
import {
  Link,
  Outlet,
  createFileRoute,
  useMatches,
} from '@tanstack/react-router'
import {
  Boxes,
  CheckCircle2,
  Filter,
  GitBranch,
  ListChecks,
  Search,
  ShieldCheck,
  UploadCloud,
  UserPlus,
  XCircle,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type { ObservatoryDataset } from '@/observatory/api/types'
import {
  getObservatoryDatasetRecords,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { StatusBadge } from '@/observatory/components/StatusBadge'
import {
  invalidateCachedResources,
  useLiveResource,
} from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/datasets')({
  component: Datasets,
})

export function Datasets() {
  const matches = useMatches()
  const result = useLiveResource(
    getObservatoryDatasetRecords,
    120_000,
    'observatory:datasets',
  )
  const isLoading = result.data === null && !result.error
  const datasets = result.data ?? []
  const [query, setQuery] = useState('')
  const [owner, setOwner] = useState('all')
  const [classification, setClassification] = useState('all')
  const [publicationState, setPublicationState] = useState('all')
  const [readinessState, setReadinessState] = useState('all')
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const promoted = datasets.filter((dataset) => !dataset.candidate)
  const candidates = datasets.filter((dataset) => dataset.candidate)
  const owners = optionValues(datasets.map((dataset) => dataset.owner))
  const classifications = optionValues(
    datasets.flatMap((dataset) => dataset.classifications),
  )
  const filtered = useMemo(
    () =>
      datasets.filter((dataset) =>
        matchesDataset(dataset, {
          classification,
          owner,
          publicationState,
          query,
          readinessState,
        }),
      ),
    [classification, datasets, owner, publicationState, query, readinessState],
  )
  const selectedDataset = filtered[0] ?? null
  const needsOwner = datasets.filter((dataset) => !dataset.owner).length
  const needsClassification = datasets.filter(
    (dataset) => dataset.classifications.length === 0,
  ).length
  const releaseBlocked = datasets.filter(
    (dataset) => dataset.readiness_state === 'error',
  ).length
  const showingDatasetDetail = matches.some(
    (match) => match.routeId === '/datasets/$datasetId',
  )

  if (showingDatasetDetail) {
    return <Outlet />
  }

  return (
    <ObservatoryPage
      kicker="Lakehouse"
      title="Datasets"
      description="Browse governed datasets first, then inspect candidate tables that look ready to be claimed."
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${datasets.length} datasets`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-dataset-queue-strip">
            <DatasetQueueMetric
              icon={<Boxes className="size-4" />}
              label="Governed"
              value={promoted.length}
              detail={`${candidates.length} candidates`}
            />
            <DatasetQueueMetric
              icon={<ShieldCheck className="size-4" />}
              label="Needs owner"
              value={needsOwner}
              detail={`${needsClassification} missing classification`}
              state={
                needsOwner > 0 || needsClassification > 0 ? 'warning' : 'ok'
              }
            />
            <DatasetQueueMetric
              icon={<UploadCloud className="size-4" />}
              label="Release blocked"
              value={releaseBlocked}
              detail={`${publishedCount(promoted)} published`}
              state={releaseBlocked > 0 ? 'error' : 'ok'}
            />
          </div>
          <div className="phlo-observatory-browser-toolbar">
            <span>
              <Boxes className="size-4" />
              Dataset queue
            </span>
            <label className="phlo-observatory-search-field">
              <Search className="size-4" />
              <input
                aria-label="Search Datasets"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search Datasets"
                value={query}
              />
            </label>
          </div>
          <div className="phlo-observatory-dataset-filters">
            <SelectFilter
              label="Owner"
              onChange={setOwner}
              value={owner}
              values={owners}
            />
            <SelectFilter
              label="Classification"
              onChange={setClassification}
              value={classification}
              values={classifications}
            />
            <SelectFilter
              label="Publication"
              onChange={setPublicationState}
              value={publicationState}
              values={['draft', 'published', 'retired']}
            />
            <SelectFilter
              label="Readiness"
              onChange={setReadinessState}
              value={readinessState}
              values={['ok', 'warning', 'error', 'unknown']}
            />
          </div>
          <DatasetList
            error={result.error}
            isLoading={isLoading}
            datasets={filtered}
          />
        </div>

        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">
            Selected dataset
          </div>
          <h2>
            {isLoading
              ? 'Loading datasets'
              : (selectedDataset?.name ?? 'No dataset selected')}
          </h2>
          <p>
            {selectedDataset
              ? datasetInspectorSummary(selectedDataset)
              : 'Use the queue to inspect readiness, ownership, publication, and candidate state.'}
          </p>
          {selectedDataset && (
            <>
              <div
                className="phlo-observatory-dataset-inspector-callout"
                data-state={selectedDataset.readiness_state}
              >
                <span>Blocker</span>
                <strong>{datasetQueueReason(selectedDataset)}</strong>
                <small>Next: {datasetNextAction(selectedDataset)}</small>
              </div>
              <dl className="phlo-observatory-facts">
                <dt>Owner</dt>
                <dd>{selectedDataset.owner ?? 'unassigned'}</dd>
                <dt>Publication</dt>
                <dd>{selectedDataset.publication_state}</dd>
                <dt>Readiness</dt>
                <dd>{selectedDataset.readiness_state}</dd>
                <dt>Classification</dt>
                <dd>{selectedDataset.classifications.join(', ') || 'none'}</dd>
              </dl>
              <Link
                className="phlo-observatory-map-action phlo-observatory-full-width-action"
                params={{ datasetId: selectedDataset.id }}
                to="/datasets/$datasetId"
              >
                <Boxes className="size-4" />
                Open Dataset profile
              </Link>
              <div className="phlo-observatory-inspector-section-label">
                Supporting evidence
              </div>
              <DatasetEvidenceLinks dataset={selectedDataset} compact />
            </>
          )}
          <div className="phlo-observatory-detail-list">
            <div className="phlo-observatory-mini-row">
              <span>Candidate datasets</span>
              <small>claim, promote, or reject</small>
            </div>
            {candidates.slice(0, 6).map((candidate) => (
              <CandidateRow
                candidate={candidate}
                key={candidate.id}
                onAction={(actionId) => {
                  setActionMessage('Requesting workflow action...')
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
              />
            ))}
            {!isLoading && candidates.length === 0 && (
              <div className="phlo-observatory-mini-row">
                <span>No candidate datasets</span>
                <small>nothing to claim</small>
              </div>
            )}
          </div>
          {actionMessage && (
            <div className="phlo-observatory-panel-footer">{actionMessage}</div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function DatasetQueueMetric({
  detail,
  icon,
  label,
  state = 'unknown',
  value,
}: {
  detail: string
  icon: ReactNode
  label: string
  state?: 'ok' | 'warning' | 'error' | 'unknown'
  value: number
}) {
  return (
    <div className="phlo-observatory-dataset-queue-metric" data-state={state}>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  )
}

function CandidateRow({
  candidate,
  onAction,
}: {
  candidate: ObservatoryDataset
  onAction: (actionId: string) => void
}) {
  const sourceId = candidate.source_refs[0]?.id ?? candidate.id
  return (
    <div className="phlo-observatory-mini-row phlo-observatory-candidate-row">
      <Link params={{ datasetId: candidate.id }} to="/datasets/$datasetId">
        <span>{candidate.name}</span>
        <small>
          {candidate.source_refs
            .map((ref) => resourceKindLabel(ref.kind))
            .join(', ') || 'source'}
        </small>
      </Link>
      <div className="phlo-observatory-inline-actions">
        <button
          onClick={() => onAction(`candidate:${sourceId}:claim`)}
          title="Assign an owner before promotion"
          type="button"
        >
          <UserPlus className="size-3.5" />
          Claim
        </button>
        <button
          onClick={() => onAction(`candidate:${sourceId}:promote`)}
          title="Promote to a governed Dataset"
          type="button"
        >
          <CheckCircle2 className="size-3.5" />
          Promote
        </button>
        <button
          onClick={() => onAction(`candidate:${sourceId}:reject`)}
          title="Hide this candidate from Dataset review"
          type="button"
        >
          <XCircle className="size-3.5" />
          Reject
        </button>
      </div>
    </div>
  )
}

function DatasetList({
  error,
  isLoading,
  datasets,
}: {
  error: string | null
  isLoading: boolean
  datasets: Array<ObservatoryDataset>
}) {
  if (isLoading) {
    return (
      <div className="phlo-observatory-operation-empty">
        <div>
          <span className="phlo-observatory-inspector-label">
            Loading Dataset inventory
          </span>
          <h2>Reading live lakehouse state.</h2>
          <p>
            Datasets, candidates, owners, classifications, and readiness filters
            will appear together.
          </p>
        </div>
      </div>
    )
  }

  if (datasets.length === 0) {
    return (
      <div className="phlo-observatory-operation-empty">
        <div>
          <span className="phlo-observatory-inspector-label">
            {error ? 'Dataset inventory unavailable' : 'No datasets in view'}
          </span>
          <h2>
            {error ? 'Datasets could not load.' : 'No promoted datasets found.'}
          </h2>
          <p>
            {error ??
              'Promote candidate Datasets when they have enough ownership, quality, and publishing evidence.'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="phlo-observatory-list phlo-observatory-dataset-table">
      <div className="phlo-observatory-dataset-head">
        <span>Dataset</span>
        <span>Status</span>
        <span>Owner</span>
        <span>Blocker</span>
        <span>Next action</span>
      </div>
      {datasets.map((dataset) => (
        <article className="phlo-observatory-dataset-row" key={dataset.id}>
          <span
            className="phlo-observatory-dot"
            data-state={dataset.readiness_state}
          />
          <div className="phlo-observatory-dataset-row-main">
            <div className="phlo-observatory-row-title">
              <Boxes className="size-4" />
              <Link
                params={{ datasetId: dataset.id }}
                to="/datasets/$datasetId"
              >
                {dataset.name}
              </Link>
            </div>
            <div className="phlo-observatory-row-meta">
              {[
                dataset.classifications.join(', ') || 'unclassified',
                dataset.candidate ? 'candidate' : 'governed',
                dataset.source_refs
                  .map((ref) => resourceKindLabel(ref.kind))
                  .join(', ') || 'source',
              ].join(' · ')}
            </div>
          </div>
          <div className="phlo-observatory-dataset-row-status">
            {dataset.candidate && (
              <span className="phlo-observatory-pill">candidate</span>
            )}
            <StatusBadge
              label={dataset.publication_state}
              state={dataset.readiness_state}
            />
          </div>
          <span className="phlo-observatory-dataset-row-owner">
            {dataset.owner ?? 'unassigned'}
          </span>
          <span className="phlo-observatory-dataset-row-blocker">
            {datasetQueueReason(dataset)}
          </span>
          <span className="phlo-observatory-dataset-row-next">
            {datasetNextAction(dataset)}
          </span>
        </article>
      ))}
    </div>
  )
}

function DatasetEvidenceLinks({
  compact = false,
  dataset,
}: {
  compact?: boolean
  dataset: ObservatoryDataset
}) {
  const lineageTarget =
    firstResourceHref(dataset, 'asset') ??
    firstResourceHref(dataset, 'table') ??
    `/lineage`
  return (
    <div
      className="phlo-observatory-dataset-evidence-links"
      data-compact={compact}
    >
      {!compact && (
        <Link
          params={{ datasetId: dataset.id }}
          title="Open quality, operations, lineage, publishing, and governance evidence"
          to="/datasets/$datasetId"
        >
          <ListChecks className="size-3.5" />
          Profile
        </Link>
      )}
      <Link to={lineageTarget} title="Open linked lineage">
        <GitBranch className="size-3.5" />
        Lineage
      </Link>
      <Link
        search={{ datasetId: dataset.id }}
        title="Open publishing policy"
        to="/publishing"
      >
        <UploadCloud className="size-3.5" />
        Publishing
      </Link>
      <Link
        search={{ datasetId: dataset.id }}
        title="Open governance controls"
        to="/governance"
      >
        <ShieldCheck className="size-3.5" />
        Governance
      </Link>
    </div>
  )
}

function SelectFilter({
  label,
  onChange,
  value,
  values,
}: {
  label: string
  onChange: (value: string) => void
  value: string
  values: Array<string>
}) {
  return (
    <label className="phlo-observatory-filter-field">
      <Filter className="size-3.5" />
      <span>{label}</span>
      <select onChange={(event) => onChange(event.target.value)} value={value}>
        <option value="all">All</option>
        {values.map((item) => (
          <option key={item} value={item}>
            {item}
          </option>
        ))}
      </select>
    </label>
  )
}

function matchesDataset(
  dataset: ObservatoryDataset,
  filters: {
    classification: string
    owner: string
    publicationState: string
    query: string
    readinessState: string
  },
) {
  const query = filters.query.trim().toLowerCase()
  const haystack = [
    dataset.name,
    dataset.description ?? '',
    dataset.owner ?? '',
    dataset.classifications.join(' '),
    dataset.source_refs.map((ref) => ref.label).join(' '),
  ]
    .join(' ')
    .toLowerCase()
  return (
    (!query || haystack.includes(query)) &&
    (filters.owner === 'all' || dataset.owner === filters.owner) &&
    (filters.classification === 'all' ||
      dataset.classifications.includes(filters.classification)) &&
    (filters.publicationState === 'all' ||
      dataset.publication_state === filters.publicationState) &&
    (filters.readinessState === 'all' ||
      dataset.readiness_state === filters.readinessState)
  )
}

function optionValues(values: Array<string | null | undefined>) {
  return Array.from(
    new Set(values.filter((value): value is string => Boolean(value))),
  ).sort()
}

function publishedCount(datasets: Array<ObservatoryDataset>): number {
  return datasets.filter((dataset) => dataset.publication_state === 'published')
    .length
}

function datasetInspectorSummary(dataset: ObservatoryDataset): string {
  if (dataset.candidate) {
    return 'Candidate dataset awaiting owner review, promotion, or rejection.'
  }
  if (dataset.readiness_state === 'error') {
    return 'Release is blocked until readiness evidence is resolved.'
  }
  if (!dataset.owner || dataset.classifications.length === 0) {
    return 'Governance evidence is incomplete before publication.'
  }
  return (
    dataset.description ?? 'Governed dataset with readiness evidence available.'
  )
}

function resourceKindLabel(kind: string): string {
  if (kind === 'asset') return 'source binding'
  return kind.replace('_', ' ')
}

function firstResourceHref(
  dataset: ObservatoryDataset,
  kind: string,
): string | null {
  const resource = dataset.source_refs.find((ref) => ref.kind === kind)
  if (!resource) return null
  if (resource.kind === 'asset') {
    return `/lineage?assetId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'table') {
    return `/tables?tableId=${encodeURIComponent(resource.id)}`
  }
  return null
}

function datasetQueueReason(dataset: ObservatoryDataset): string {
  if (dataset.candidate) {
    return 'Candidate table needs review before it becomes governed.'
  }
  if (dataset.readiness_state === 'error') {
    return 'Publication is blocked by unresolved readiness evidence.'
  }
  if (!dataset.owner) {
    return 'Ownership is missing.'
  }
  if (dataset.classifications.length === 0) {
    return 'Classification is missing.'
  }
  if (dataset.readiness_state === 'warning') {
    return 'Readiness has warnings that should be reviewed.'
  }
  return 'Readiness evidence is available.'
}

function datasetNextAction(dataset: ObservatoryDataset): string {
  if (dataset.candidate) return 'claim, promote, or reject'
  if (dataset.readiness_state === 'error') return 'open readiness cockpit'
  if (!dataset.owner) return 'assign owner'
  if (dataset.classifications.length === 0) return 'declare classification'
  if (dataset.readiness_state === 'warning') return 'review warning evidence'
  if (dataset.publication_state === 'draft')
    return 'review publishing readiness'
  return 'monitor evidence'
}
