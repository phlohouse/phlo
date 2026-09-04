/**
 * /datasets route. Parent layout listing promoted datasets and candidates
 * with owner, classification, publication, readiness, and candidate filters;
 * the selected dataset's child route renders in the outlet.
 *
 * Filter and query state is authoritative in the URL (TanStack validateSearch)
 * and applied server-side by phlo-api before pagination; the route pages
 * through the full filtered collection by consuming `next_cursor` explicitly
 * instead of trusting a client-side cap.
 */
import {
  Link,
  Outlet,
  createFileRoute,
  useMatches,
  useNavigate,
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
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryDataset,
  ObservatoryDatasetFacets,
} from '@/observatory/api/types'
import type {
  DatasetCandidateFilter,
  DatasetFilters,
} from '@/observatory/api/datasetDiscovery'
import {
  getObservatoryDatasetFacets,
  getObservatoryDatasetPage,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import {
  createRequestGuard,
  defaultDatasetPageLimit,
  serializeDatasetFilters,
  walkDatasetPages,
} from '@/observatory/api/datasetDiscovery'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { StatusBadge } from '@/observatory/components/StatusBadge'
import { invalidateCachedResources } from '@/observatory/routes/liveResource'

type DatasetsSearch = {
  q?: string
  owner?: string
  classification?: string
  publicationState?: string
  readinessState?: string
  candidate?: 'true' | 'false'
}

function validateSearch(search: Record<string, unknown>): DatasetsSearch {
  const stringParam = (value: unknown) =>
    typeof value === 'string' && value ? value : undefined
  return {
    q: stringParam(search.q),
    owner: stringParam(search.owner),
    classification: stringParam(search.classification),
    publicationState: stringParam(search.publicationState),
    readinessState: stringParam(search.readinessState),
    candidate:
      search.candidate === 'true' || search.candidate === 'false'
        ? search.candidate
        : undefined,
  }
}

export const Route = createFileRoute('/datasets')({
  component: Datasets,
  validateSearch,
})

export function Datasets() {
  const matches = useMatches()
  const navigate = useNavigate()
  const search = Route.useSearch()
  const filters: DatasetFilters = useMemo(
    () => ({
      query: search.q ?? '',
      owner: search.owner ?? 'all',
      classification: search.classification ?? 'all',
      publicationState: search.publicationState ?? 'all',
      readinessState: search.readinessState ?? 'all',
      candidate: (search.candidate ?? 'all') as DatasetCandidateFilter,
    }),
    [
      search.q,
      search.owner,
      search.classification,
      search.publicationState,
      search.readinessState,
      search.candidate,
    ],
  )

  // Cursor-aware collection state: what was loaded from phlo-api, the
  // continuation cursor, and whether the bounded walk stopped early. Counts
  // derived from this state are honest loaded counts, never totals.
  const [datasets, setDatasets] = useState<Array<ObservatoryDataset>>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [collectionError, setCollectionError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [facets, setFacets] = useState<ObservatoryDatasetFacets | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  // Bumping this re-runs the collection walk after a candidate action.
  const [refreshTick, setRefreshTick] = useState(0)

  // Stale-page guard: a walk request bumps the generation, and any response
  // from an earlier generation is discarded instead of corrupting the newer
  // query's state.
  const guardRef = useRef<ReturnType<typeof createRequestGuard> | null>(null)
  if (guardRef.current === null) {
    guardRef.current = createRequestGuard()
  }

  // Full-collection facets for filter choices; computed by phlo-api across
  // the whole Dataset collection, independent of the loaded pages.
  useEffect(() => {
    let cancelled = false
    void getObservatoryDatasetFacets().then((result) => {
      if (!cancelled) setFacets(result.data)
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const guard = guardRef.current
    let cancelled = false
    const timer = window.setTimeout(
      () => {
        const token = guard?.begin()
        void walkDatasetPages({
          fetchPage: async ({ cursor, filters: pageFilters, limit }) => {
            const result = await getObservatoryDatasetPage({
              cursor,
              filters: pageFilters,
              limit,
            })
            return {
              items: result.data?.items ?? [],
              nextCursor: result.data?.nextCursor ?? null,
              error: result.error,
            }
          },
          filters,
          limit: defaultDatasetPageLimit,
        }).then((walk) => {
          if (cancelled || (token !== undefined && !guard?.isCurrent(token))) {
            return
          }
          setDatasets(walk.items)
          setNextCursor(walk.nextCursor)
          setCollectionError(
            walk.items.length === 0 ? (walk.errors[0] ?? null) : null,
          )
          setIsLoading(false)
        })
      },
      // Debounce so typing in the query field does not fire a walk per
      // keystroke; the URL is already authoritative.
      180,
    )
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [filters, refreshTick])

  const loadMore = () => {
    const guard = guardRef.current
    if (!nextCursor || isLoadingMore) return
    const token = guard?.begin()
    setIsLoadingMore(true)
    void walkDatasetPages({
      cursor: nextCursor,
      fetchPage: async ({ cursor, filters: pageFilters, limit }) => {
        const result = await getObservatoryDatasetPage({
          cursor,
          filters: pageFilters,
          limit,
        })
        return {
          items: result.data?.items ?? [],
          nextCursor: result.data?.nextCursor ?? null,
          error: result.error,
        }
      },
      filters,
      limit: defaultDatasetPageLimit,
    }).then((walk) => {
      if (token !== undefined && !guard?.isCurrent(token)) return
      setDatasets((prev) => [...prev, ...walk.items])
      setNextCursor(walk.nextCursor)
      setCollectionError(
        walk.items.length === 0 ? (walk.errors[0] ?? null) : null,
      )
      setIsLoadingMore(false)
    })
  }

  // URL-authoritative filter updates: serialize only non-default values so
  // the query string is a shareable description of the view.
  const updateFilter = (patch: Partial<DatasetFilters>) => {
    const params = serializeDatasetFilters({ ...filters, ...patch })
    void navigate({
      replace: true,
      search: Object.fromEntries(params),
      to: '/datasets',
    })
  }
  const setQuery = (value: string) => updateFilter({ query: value })
  const setOwner = (value: string) => updateFilter({ owner: value })
  const setClassification = (value: string) =>
    updateFilter({ classification: value })
  const setPublicationState = (value: string) =>
    updateFilter({ publicationState: value })
  const setReadinessState = (value: string) =>
    updateFilter({ readinessState: value })
  const setCandidate = (value: string) =>
    updateFilter({
      candidate: value === 'true' || value === 'false' ? value : 'all',
    })

  const promoted = datasets.filter((dataset) => !dataset.candidate)
  const candidates = datasets.filter((dataset) => dataset.candidate)
  // Facet choices come from the full-collection facets endpoint; fall back to
  // whatever the loaded pages contain while facets are unavailable.
  const owners =
    facets?.owners ?? optionValues(datasets.map((dataset) => dataset.owner))
  const classifications =
    facets?.classifications ??
    optionValues(datasets.flatMap((dataset) => dataset.classifications))
  const publicationStates = facets?.publication_states ?? [
    'draft',
    'published',
    'retired',
  ]
  const readinessStates = facets?.readiness_states ?? [
    'ok',
    'warning',
    'error',
    'unknown',
  ]
  // Server-side filtering means the loaded array is already the filtered
  // collection; no client-side re-filtering that could hide matches.
  const filtered = datasets
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
          {isLoading
            ? 'Loading'
            : `${datasets.length} loaded${nextCursor ? ' · more available' : ''}`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-dataset-queue-strip">
            <DatasetQueueMetric
              icon={<Boxes className="size-4" />}
              label="Governed"
              value={loadedCount(promoted.length, nextCursor)}
              detail={`${candidates.length} candidates`}
            />
            <DatasetQueueMetric
              icon={<ShieldCheck className="size-4" />}
              label="Needs owner"
              value={loadedCount(needsOwner, nextCursor)}
              detail={`${needsClassification} missing classification`}
              state={
                needsOwner > 0 || needsClassification > 0 ? 'warning' : 'ok'
              }
            />
            <DatasetQueueMetric
              icon={<UploadCloud className="size-4" />}
              label="Release blocked"
              value={loadedCount(releaseBlocked, nextCursor)}
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
                value={filters.query}
              />
            </label>
          </div>
          <div className="phlo-observatory-dataset-filters">
            <SelectFilter
              label="Owner"
              onChange={setOwner}
              value={filters.owner}
              values={owners}
            />
            <SelectFilter
              label="Classification"
              onChange={setClassification}
              value={filters.classification}
              values={classifications}
            />
            <SelectFilter
              label="Publication"
              onChange={setPublicationState}
              value={filters.publicationState}
              values={publicationStates}
            />
            <SelectFilter
              label="Readiness"
              onChange={setReadinessState}
              value={filters.readinessState}
              values={readinessStates}
            />
            <SelectFilter
              labels={{ false: 'Governed only', true: 'Candidates only' }}
              label="Candidate"
              onChange={setCandidate}
              value={filters.candidate}
              values={['true', 'false']}
            />
          </div>
          <DatasetList
            error={collectionError}
            isLoading={isLoading}
            datasets={filtered}
          />
          {!isLoading && nextCursor !== null && (
            <button
              className="phlo-observatory-load-more"
              disabled={isLoadingMore}
              onClick={loadMore}
              type="button"
            >
              {isLoadingMore
                ? 'Loading more…'
                : `Load more datasets (${datasets.length} loaded)`}
            </button>
          )}
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
                    // Re-run the cursor-aware collection walk; the old
                    // liveResource focus refresh no longer applies here.
                    setRefreshTick((tick) => tick + 1)
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
  value: string | number
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
  labels = {},
  label,
  onChange,
  value,
  values,
}: {
  labels?: Record<string, string>
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
            {labels[item] ?? item}
          </option>
        ))}
      </select>
    </label>
  )
}

/**
 * Honest loaded count: while a next cursor remains, the loaded array is a
 * prefix of the collection, so counts are shown as lower bounds rather than
 * fabricated totals.
 */
function loadedCount(value: number, hasMore: string | null): string {
  return hasMore !== null && value > 0 ? `≥${value}` : String(value)
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
