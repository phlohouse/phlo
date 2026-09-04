/**
 * Client-side Dataset and Search discovery helpers for the Observatory
 * Catalog.
 *
 * phlo-api applies filters server-side before pagination and reports the
 * continuation as `next_cursor` on the list envelope. `getRawCollection`
 * strips that envelope, so routes compose the envelope-preserving getters in
 * resources.ts with the pure helpers here: URL filter round-trips, bounded
 * page traversal that consumes `next_cursor` explicitly, request guards that
 * keep stale page responses from corrupting a newer query, and search facet
 * choices that do not depend on the currently displayed page.
 */
import type { ObservatoryDataset, ObservatorySearchResult } from './types'

export type DatasetCandidateFilter = 'all' | 'true' | 'false'

export interface DatasetFilters {
  query: string
  owner: string
  classification: string
  publicationState: string
  readinessState: string
  candidate: DatasetCandidateFilter
}

export interface SearchFilters {
  query: string
  kind: string
  owner: string
}

export const defaultDatasetFilters: DatasetFilters = {
  query: '',
  owner: 'all',
  classification: 'all',
  publicationState: 'all',
  readinessState: 'all',
  candidate: 'all',
}

export const defaultSearchFilters: SearchFilters = {
  query: '',
  kind: 'all',
  owner: 'all',
}

/**
 * Bounded traversal limit: how many API pages one collection load may
 * consume before it stops and reports `truncated: true` with the remaining
 * cursor. Bounds protect the UI from pathological collections; the cursor
 * always remains available for an explicit "load more".
 */
export const defaultDatasetPageLimit = 100
export const defaultMaxPages = 3

export function parseDatasetFilters(params: URLSearchParams): DatasetFilters {
  const candidate = params.get('candidate')
  return {
    query: params.get('q') ?? defaultDatasetFilters.query,
    owner: params.get('owner') ?? defaultDatasetFilters.owner,
    classification:
      params.get('classification') ?? defaultDatasetFilters.classification,
    publicationState:
      params.get('publicationState') ?? defaultDatasetFilters.publicationState,
    readinessState:
      params.get('readinessState') ?? defaultDatasetFilters.readinessState,
    candidate:
      candidate === 'true' || candidate === 'false'
        ? candidate
        : defaultDatasetFilters.candidate,
  }
}

/**
 * Serialize only the non-default filters so the URL stays a faithful,
 * shareable description of the view: default values are simply absent, and
 * `parseDatasetFilters(serializeDatasetFilters(filters))` is the identity.
 */
export function serializeDatasetFilters(
  filters: DatasetFilters,
): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.query.trim()) params.set('q', filters.query.trim())
  if (filters.owner !== defaultDatasetFilters.owner) {
    params.set('owner', filters.owner)
  }
  if (filters.classification !== defaultDatasetFilters.classification) {
    params.set('classification', filters.classification)
  }
  if (filters.publicationState !== defaultDatasetFilters.publicationState) {
    params.set('publicationState', filters.publicationState)
  }
  if (filters.readinessState !== defaultDatasetFilters.readinessState) {
    params.set('readinessState', filters.readinessState)
  }
  if (filters.candidate !== defaultDatasetFilters.candidate) {
    params.set('candidate', filters.candidate)
  }
  return params
}

/**
 * Query string for one phlo-api Dataset page. Filter and query parameters are
 * supplied with every page request so server-side, filter-before-pagination
 * cursors stay stable.
 */
export function datasetPageQuery(
  filters: DatasetFilters,
  limit: number,
  cursor: string | null,
): string {
  const params = serializeDatasetFilters(filters)
  params.set('limit', String(limit))
  if (cursor) params.set('cursor', cursor)
  const query = params.toString()
  return query ? `?${query}` : ''
}

export function parseSearchFilters(params: URLSearchParams): SearchFilters {
  return {
    query: params.get('q') ?? defaultSearchFilters.query,
    kind: params.get('kind') ?? defaultSearchFilters.kind,
    owner: params.get('owner') ?? defaultSearchFilters.owner,
  }
}

export function serializeSearchFilters(
  filters: SearchFilters,
): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.query.trim()) params.set('q', filters.query.trim())
  if (filters.kind !== defaultSearchFilters.kind) {
    params.set('kind', filters.kind)
  }
  if (filters.owner !== defaultSearchFilters.owner) {
    params.set('owner', filters.owner)
  }
  return params
}

/**
 * Query string for one phlo-api search page. `q`, `kind`, and `owner` are
 * supplied with every page request so server-side, filter-before-pagination
 * cursors stay stable.
 */
export function searchPageQuery(
  filters: SearchFilters,
  limit: number,
  cursor: string | null,
): string {
  const params = serializeSearchFilters(filters)
  params.set('limit', String(limit))
  if (cursor) params.set('cursor', cursor)
  const query = params.toString()
  return query ? `?${query}` : ''
}

export interface PageWalkResult<T> {
  items: Array<T>
  /** Continuation cursor for the next page, or null when exhausted. */
  nextCursor: string | null
  pagesLoaded: number
  /** True when the walk stopped early (page cap or failure) with more work. */
  truncated: boolean
  /** Transport errors reported by failed pages, in page order. */
  errors: Array<string>
}

export type FetchDatasetPage = (args: {
  cursor: string | null
  filters: DatasetFilters
  limit: number
}) => Promise<{
  items: Array<ObservatoryDataset>
  nextCursor: string | null
  error?: string | null
}>

export type FetchSearchPage = (args: {
  cursor: string | null
  filters: SearchFilters
  limit: number
}) => Promise<{
  items: Array<ObservatorySearchResult>
  nextCursor: string | null
  error?: string | null
}>

async function walkPages<TItem, TFilters>(
  fetchPage: (args: {
    cursor: string | null
    filters: TFilters
    limit: number
  }) => Promise<{
    items: Array<TItem>
    nextCursor: string | null
    error?: string | null
  }>,
  {
    cursor,
    filters,
    limit,
    maxPages,
  }: {
    cursor: string | null
    filters: TFilters
    limit: number
    maxPages: number
  },
): Promise<PageWalkResult<TItem>> {
  const items: Array<TItem> = []
  const errors: Array<string> = []
  let nextCursor = cursor
  let pagesLoaded = 0

  while (pagesLoaded < maxPages) {
    const page = await fetchPage({
      cursor: nextCursor,
      filters,
      limit,
    })
    items.push(...page.items)
    if (page.error) errors.push(page.error)
    pagesLoaded += 1
    if (page.error) {
      // A failed page ends the walk without claiming exhaustion: the cursor
      // that failed stays available so an explicit retry can resume there.
      return {
        items,
        errors,
        nextCursor: nextCursor,
        pagesLoaded,
        truncated: true,
      }
    }
    nextCursor = page.nextCursor
    if (!nextCursor) {
      return { items, errors, nextCursor: null, pagesLoaded, truncated: false }
    }
  }

  return { items, errors, nextCursor, pagesLoaded, truncated: true }
}

/**
 * Load up to `maxPages` Dataset pages, consuming `next_cursor` explicitly so
 * matches are not lost to a client-side cap. Never fabricates a total: the
 * result reports what was loaded plus whether more pages remain.
 */
export function walkDatasetPages({
  cursor = null,
  fetchPage,
  filters,
  limit = defaultDatasetPageLimit,
  maxPages = defaultMaxPages,
}: {
  cursor?: string | null
  fetchPage: FetchDatasetPage
  filters: DatasetFilters
  limit?: number
  maxPages?: number
}): Promise<PageWalkResult<ObservatoryDataset>> {
  return walkPages(fetchPage, { cursor, filters, limit, maxPages })
}

/**
 * Load up to `maxPages` search pages, consuming `next_cursor` explicitly.
 */
export function walkSearchPages({
  cursor = null,
  fetchPage,
  filters,
  limit = defaultDatasetPageLimit,
  maxPages = defaultMaxPages,
}: {
  cursor?: string | null
  fetchPage: FetchSearchPage
  filters: SearchFilters
  limit?: number
  maxPages?: number
}): Promise<PageWalkResult<ObservatorySearchResult>> {
  return walkPages(fetchPage, { cursor, filters, limit, maxPages })
}

export interface RequestGuard {
  /**
   * Start a new request generation. Only responses whose token equals the
   * latest generation may be applied.
   */
  begin: () => number
  isCurrent: (token: number) => boolean
}

/**
 * Stale-page guard for hand-rolled request streams: each `begin()` bumps the
 * generation, and any in-flight response from an earlier generation is
 * discarded by the caller instead of corrupting the newer query's state.
 */
export function createRequestGuard(): RequestGuard {
  let generation = 0
  return {
    begin() {
      generation += 1
      return generation
    },
    isCurrent(token: number) {
      return token === generation
    },
  }
}

export function searchFacetChoices(results: Array<ObservatorySearchResult>): {
  kinds: Array<string>
  owners: Array<string>
} {
  const kinds = new Set<string>()
  const owners = new Set<string>()
  for (const result of results) {
    kinds.add(result.kind)
    const owner = result.metadata.owner ?? result.metadata.created_by
    if (typeof owner === 'string' && owner.trim()) owners.add(owner.trim())
  }
  return {
    kinds: Array.from(kinds).sort(),
    owners: Array.from(owners).sort(),
  }
}
