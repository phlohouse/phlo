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
  GitBranch,
  ListChecks,
  Search,
  ShieldCheck,
  UploadCloud,
  UserPlus,
  XCircle,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type {
  ObservatoryDataset,
  ObservatoryDatasetFacets,
  ObservatoryPublishingReadiness,
} from '@/observatory/api/types'
import type {
  DatasetCandidateFilter,
  DatasetFilters,
} from '@/observatory/api/datasetDiscovery'
import {
  getObservatoryDatasetFacets,
  getObservatoryDatasetPage,
  getObservatoryPublishingReadinessDirect,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import {
  createRequestGuard,
  defaultDatasetPageLimit,
  serializeDatasetFilters,
  walkDatasetPages,
} from '@/observatory/api/datasetDiscovery'
import { invalidateCachedResources } from '@/observatory/routes/liveResource'
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
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

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
  // Canonical publication readiness per dataset, served by phlo-api's bulk
  // readiness endpoint (the canonical verdict). Rows render these reasons
  // verbatim and never infer blockers from owner or classification fields.
  const [readinessMap, setReadinessMap] = useState<
    Record<string, ObservatoryPublishingReadiness | undefined>
  >({})
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

  // One bulk request serves the canonical readiness verdict for the
  // inspector and row reasons; no per-dataset eligibility is computed here.
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
    })
    return () => {
      cancelled = true
    }
  }, [refreshTick])

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
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading
              ? 'Loading'
              : `${datasets.length} loaded${nextCursor ? ' · more available' : ''}`}
          </Badge>
        }
        description="Browse governed datasets first, then inspect candidate tables that look ready to be claimed."
        title="Datasets"
      />
      <StatGrid className="xl:grid-cols-3">
        <StatCard
          note={`${candidates.length} candidates`}
          label="Governed"
          value={loadedCount(promoted.length, nextCursor)}
        />
        <StatCard
          note={`${needsClassification} missing classification`}
          label="Needs owner"
          state={needsOwner > 0 || needsClassification > 0 ? 'warning' : 'ok'}
          value={loadedCount(needsOwner, nextCursor)}
        />
        <StatCard
          note={`${publishedCount(promoted)} published`}
          label="Release blocked"
          state={releaseBlocked > 0 ? 'error' : 'ok'}
          value={loadedCount(releaseBlocked, nextCursor)}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection
              label={`Selected dataset · ${selectedDataset?.name ?? 'none'}`}
            >
              {isLoading ? (
                <LoadingBlock className="p-3" label="Loading datasets" />
              ) : selectedDataset ? (
                <>
                  <p className="text-muted-foreground text-xs/relaxed">
                    {canonicalInspectorSummary(
                      selectedDataset,
                      readinessMap[selectedDataset.id] ?? null,
                    )}
                  </p>
                  <div
                    className={cn(
                      'flex flex-col gap-1 border px-3 py-2',
                      selectedDataset.readiness_state === 'error' &&
                        'border-status-error/40 bg-status-error/5',
                      selectedDataset.readiness_state === 'warning' &&
                        'border-status-warning/40 bg-status-warning/5',
                      selectedDataset.readiness_state === 'ok' &&
                        'border-status-ok/40 bg-status-ok/5',
                      (selectedDataset.readiness_state === 'unknown' ||
                        !['ok', 'warning', 'error'].includes(
                          selectedDataset.readiness_state,
                        )) &&
                        'border-border bg-muted/30',
                    )}
                    data-state={selectedDataset.readiness_state}
                  >
                    <span className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
                      Blocker
                    </span>
                    <strong className="text-foreground text-xs">
                      {canonicalQueueReason(
                        selectedDataset,
                        readinessMap[selectedDataset.id] ?? null,
                      )}
                    </strong>
                    <span className="text-muted-foreground font-mono text-[10px]">
                      Next:{' '}
                      {canonicalNextAction(
                        selectedDataset,
                        readinessMap[selectedDataset.id] ?? null,
                      )}
                    </span>
                  </div>
                  <FactGrid>
                    <Fact
                      label="Owner"
                      value={selectedDataset.owner ?? 'unassigned'}
                    />
                    <Fact
                      label="Publication"
                      value={selectedDataset.publication_state}
                    />
                    <Fact
                      label="Readiness"
                      value={selectedDataset.readiness_state}
                    />
                    <Fact
                      label="Classification"
                      value={
                        selectedDataset.classifications.join(', ') || 'none'
                      }
                    />
                  </FactGrid>
                  <Button
                    nativeButton={false}
                    render={
                      <Link
                        params={{ datasetId: selectedDataset.id }}
                        to="/datasets/$datasetId"
                      />
                    }
                    size="sm"
                    variant="outline"
                  >
                    <Boxes className="size-3.5" />
                    Open Dataset profile
                  </Button>
                  <DatasetEvidenceLinks dataset={selectedDataset} compact />
                </>
              ) : (
                <p className="text-muted-foreground text-xs">
                  Use the queue to inspect readiness, ownership, publication,
                  and candidate state.
                </p>
              )}
            </InspectorSection>
            <InspectorSection label="Candidate datasets">
              <p className="text-muted-foreground font-mono text-[10px]">
                claim, promote, or reject
              </p>
              <div className="divide-border -mx-3 divide-y">
                {candidates.slice(0, 6).map((candidate) => (
                  <CandidateRow
                    candidate={candidate}
                    key={candidate.id}
                    onAction={(actionId) => {
                      setActionMessage('Requesting workflow action...')
                      void runObservatoryActionDirect({ actionId }).then(
                        (next) => {
                          invalidateCachedResources([
                            'observatory:datasets',
                            'observatory:operations',
                          ])
                          // Re-run the cursor-aware collection walk; the old
                          // liveResource focus refresh no longer applies here.
                          setRefreshTick((tick) => tick + 1)
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
                {!isLoading && candidates.length === 0 && (
                  <p className="text-muted-foreground px-3 py-2 text-xs">
                    No candidate datasets — nothing to claim.
                  </p>
                )}
              </div>
              {actionMessage && (
                <p className="text-muted-foreground pt-2 font-mono text-[10px] break-all">
                  {actionMessage}
                </p>
              )}
            </InspectorSection>
          </>
        }
        list={
          <div className="bg-sheet flex min-h-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center gap-2 border-b p-2">
              <span className="text-muted-foreground flex items-center gap-1.5 px-1 text-[10px] font-medium tracking-widest uppercase">
                <Boxes className="size-3.5" />
                Dataset queue
              </span>
              <div className="relative min-w-48 flex-1">
                <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
                <Input
                  aria-label="Search Datasets"
                  className="pl-7"
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search Datasets"
                  value={filters.query}
                />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 border-b px-2 py-1.5">
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
              readinessMap={readinessMap}
            />
            {!isLoading && nextCursor !== null && (
              <div className="border-t p-2">
                <Button
                  className="w-full"
                  disabled={isLoadingMore}
                  onClick={loadMore}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  {isLoadingMore
                    ? 'Loading more…'
                    : `Load more datasets (${datasets.length} loaded)`}
                </Button>
              </div>
            )}
          </div>
        }
      />
    </Page>
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
    <div className="flex items-center justify-between gap-2 px-3 py-2">
      <Link
        className="min-w-0"
        params={{ datasetId: candidate.id }}
        to="/datasets/$datasetId"
      >
        <span className="text-foreground block truncate text-xs font-medium hover:underline">
          {candidate.name}
        </span>
        <span className="text-muted-foreground block font-mono text-[10px]">
          {candidate.source_refs
            .map((ref) => resourceKindLabel(ref.kind))
            .join(', ') || 'source'}
        </span>
      </Link>
      <div className="flex flex-none items-center gap-1">
        <Button
          onClick={() => onAction(`candidate:${sourceId}:claim`)}
          size="xs"
          title="Assign an owner before promotion"
          type="button"
          variant="outline"
        >
          <UserPlus className="size-3" />
          Claim
        </Button>
        <Button
          onClick={() => onAction(`candidate:${sourceId}:promote`)}
          size="xs"
          title="Promote to a governed Dataset"
          type="button"
          variant="outline"
        >
          <CheckCircle2 className="size-3" />
          Promote
        </Button>
        <Button
          onClick={() => onAction(`candidate:${sourceId}:reject`)}
          size="xs"
          title="Hide this candidate from Dataset review"
          type="button"
          variant="ghost"
        >
          <XCircle className="size-3" />
          Reject
        </Button>
      </div>
    </div>
  )
}

function DatasetList({
  error,
  isLoading,
  datasets,
  readinessMap,
}: {
  error: string | null
  isLoading: boolean
  datasets: Array<ObservatoryDataset>
  readinessMap: Record<string, ObservatoryPublishingReadiness | undefined>
}) {
  if (isLoading) {
    return (
      <LoadingBlock
        className="p-3"
        label="Reading live lakehouse state. Datasets, candidates, owners, classifications, and readiness filters will appear together."
      />
    )
  }

  if (datasets.length === 0) {
    return (
      <EmptyBlock
        className="py-10"
        description={
          error ??
          'Promote candidate Datasets when they have enough ownership, quality, and publishing evidence.'
        }
        title={
          error ? 'Datasets could not load.' : 'No promoted datasets found.'
        }
      />
    )
  }

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="text-muted-foreground grid grid-cols-[minmax(0,1.5fr)_7rem_minmax(0,6rem)_minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase max-lg:grid-cols-[minmax(0,1.5fr)_7rem_minmax(0,1fr)]">
        <span>Dataset</span>
        <span>Status</span>
        <span className="max-lg:hidden">Owner</span>
        <span>Blocker</span>
        <span className="max-lg:hidden">Next action</span>
      </div>
      {datasets.map((dataset) => (
        <article
          className="hover:bg-accent/40 grid grid-cols-[minmax(0,1.5fr)_7rem_minmax(0,6rem)_minmax(0,1fr)_minmax(0,1fr)] items-center gap-3 border-b px-3 py-2 transition-colors max-lg:grid-cols-[minmax(0,1.5fr)_7rem_minmax(0,1fr)]"
          key={dataset.id}
        >
          <div className="flex min-w-0 items-start gap-2">
            <span
              className="status-dot mt-1.5 flex-none"
              data-state={dataset.readiness_state}
            />
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-medium">
                <Boxes className="text-muted-foreground size-3.5 flex-none" />
                <Link
                  className="text-foreground truncate hover:underline"
                  params={{ datasetId: dataset.id }}
                  to="/datasets/$datasetId"
                >
                  {dataset.name}
                </Link>
              </div>
              <div className="text-muted-foreground mt-0.5 truncate font-mono text-[10px]">
                {[
                  dataset.classifications.join(', ') || 'unclassified',
                  dataset.candidate ? 'candidate' : 'governed',
                  dataset.source_refs
                    .map((ref) => resourceKindLabel(ref.kind))
                    .join(', ') || 'source',
                ].join(' · ')}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {dataset.candidate && <Badge variant="outline">candidate</Badge>}
            <StatusBadge
              label={dataset.publication_state}
              state={dataset.readiness_state}
            />
          </div>
          <span className="text-muted-foreground truncate font-mono text-[10px] max-lg:hidden">
            {dataset.owner ?? 'unassigned'}
          </span>
          <span className="text-muted-foreground truncate text-[10px]">
            {canonicalQueueReason(dataset, readinessMap[dataset.id] ?? null)}
          </span>
          <span className="text-muted-foreground truncate font-mono text-[10px] max-lg:hidden">
            {canonicalNextAction(dataset, readinessMap[dataset.id] ?? null)}
          </span>
        </article>
      ))}
    </ScrollArea>
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
    <div className="flex flex-wrap items-center gap-1.5">
      {!compact && (
        <Button
          nativeButton={false}
          render={
            <Link
              params={{ datasetId: dataset.id }}
              title="Open quality, operations, lineage, publishing, and governance evidence"
              to="/datasets/$datasetId"
            />
          }
          size="xs"
          variant="outline"
        >
          <ListChecks className="size-3.5" />
          Profile
        </Button>
      )}
      <Button
        nativeButton={false}
        render={<Link title="Open linked lineage" to={lineageTarget} />}
        size="xs"
        variant="outline"
      >
        <GitBranch className="size-3.5" />
        Lineage
      </Button>
      <Button
        nativeButton={false}
        render={
          <Link
            search={{ datasetId: dataset.id }}
            title="Open publishing policy"
            to="/publishing"
          />
        }
        size="xs"
        variant="outline"
      >
        <UploadCloud className="size-3.5" />
        Publishing
      </Button>
      <Button
        nativeButton={false}
        render={
          <Link
            search={{ datasetId: dataset.id }}
            title="Open governance controls"
            to="/governance"
          />
        }
        size="xs"
        variant="outline"
      >
        <ShieldCheck className="size-3.5" />
        Governance
      </Button>
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
    <label className="text-muted-foreground flex items-center gap-1.5 font-mono text-[10px]">
      <span className="font-medium tracking-widest uppercase">{label}</span>
      <select
        className="border-input bg-background h-7 border px-1.5 text-[11px]"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
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

/**
 * Canonical-only queue reason: the blocker, evidence gap, or
 * warning comes verbatim from the canonical readiness verdict phlo-api
 * serves. Candidates are a server identity fact. Nothing is inferred from
 * owner or classification fields, and an unloaded verdict renders as
 * pending rather than assumed-clear.
 */
function canonicalQueueReason(
  dataset: ObservatoryDataset,
  readiness: ObservatoryPublishingReadiness | null,
): string {
  if (dataset.candidate) {
    return 'Candidate table needs review before it becomes governed.'
  }
  if (!readiness) {
    return 'Canonical readiness loading from phlo-api.'
  }
  return (
    readiness.blockers[0] ??
    readiness.missing_evidence[0] ??
    readiness.warnings[0] ??
    'Readiness evidence is available.'
  )
}

function canonicalNextAction(
  dataset: ObservatoryDataset,
  readiness: ObservatoryPublishingReadiness | null,
): string {
  if (dataset.candidate) return 'claim, promote, or reject'
  if (!readiness) return 'await canonical readiness'
  if (readiness.blockers.length > 0) return 'resolve release blockers'
  if (readiness.missing_evidence.length > 0) return 'collect evidence'
  if (dataset.publication_state === 'published') return 'retire if obsolete'
  if (readiness.warnings.length > 0) return 'review warning evidence'
  return 'review publishing readiness'
}

function canonicalInspectorSummary(
  dataset: ObservatoryDataset,
  readiness: ObservatoryPublishingReadiness | null,
): string {
  if (dataset.candidate) {
    return 'Candidate dataset awaiting owner review, promotion, or rejection.'
  }
  if (readiness && readiness.blockers.length > 0) {
    return 'Release is blocked by the canonical readiness verdict.'
  }
  if (readiness && readiness.missing_evidence.length > 0) {
    return 'Canonical readiness is waiting on missing evidence.'
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
