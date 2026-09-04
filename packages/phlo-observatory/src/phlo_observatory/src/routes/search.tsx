/**
 * /search route. Debounced full-text search against the Observatory API.
 *
 * Query, kind, and owner filters are authoritative in the URL (TanStack
 * validateSearch) and applied server-side by phlo-api before pagination, so
 * the result list is never re-filtered client-side. Results traverse
 * `next_cursor` with a bounded walk plus an explicit "Load more", and the
 * kind/owner facet choices come from a bounded walk of the unfiltered
 * collection so they do not collapse to one filtered page.
 */
import { Link, createFileRoute, useNavigate } from '@tanstack/react-router'
import { Search as SearchIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { ObservatorySearchResult } from '@/observatory/api/types'
import type { SearchFilters } from '@/observatory/api/datasetDiscovery'
import { searchObservatoryPage } from '@/observatory/api/resources'
import {
  createRequestGuard,
  searchFacetChoices,
  serializeSearchFilters,
  walkSearchPages,
} from '@/observatory/api/datasetDiscovery'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

type SearchRouteSearch = {
  q?: string
  kind?: string
  owner?: string
}

function validateSearch(search: Record<string, unknown>): SearchRouteSearch {
  const stringParam = (value: unknown) =>
    typeof value === 'string' && value ? value : undefined
  return {
    q: stringParam(search.q),
    kind: stringParam(search.kind),
    owner: stringParam(search.owner),
  }
}

export const Route = createFileRoute('/search')({
  component: SearchResults,
  validateSearch,
})

// How many API pages the initial result walk and the facet walk may consume.
const resultMaxPages = 2
const facetMaxPages = 2

export function SearchResults() {
  const navigate = useNavigate()
  const search = Route.useSearch()
  const filters: SearchFilters = useMemo(
    () => ({
      query: search.q ?? '',
      kind: search.kind ?? 'all',
      owner: search.owner ?? 'all',
    }),
    [search.q, search.kind, search.owner],
  )

  const [results, setResults] = useState<Array<ObservatorySearchResult>>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [facetsResults, setFacetsResults] = useState<
    Array<ObservatorySearchResult>
  >([])
  const [message, setMessage] = useState(
    filters.query.trim().length >= 2
      ? 'Searching…'
      : 'Enter at least two characters.',
  )
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const guardRef = useRef<ReturnType<typeof createRequestGuard> | null>(null)
  if (guardRef.current === null) {
    guardRef.current = createRequestGuard()
  }

  const fetchPage = async ({
    cursor,
    filters: pageFilters,
    limit,
  }: {
    cursor: string | null
    filters: SearchFilters
    limit: number
  }) => {
    const response = await searchObservatoryPage({
      cursor,
      filters: pageFilters,
      limit,
    })
    return {
      items: response.data?.items ?? [],
      nextCursor: response.data?.nextCursor ?? null,
      error: response.error,
    }
  }

  useEffect(() => {
    const guard = guardRef.current
    if (filters.query.trim().length < 2) {
      setResults([])
      setNextCursor(null)
      setMessage('Enter at least two characters.')
      return
    }
    let cancelled = false
    const timer = window.setTimeout(
      () => {
        const token = guard?.begin()
        void walkSearchPages({
          fetchPage,
          filters,
          maxPages: resultMaxPages,
        }).then((walk) => {
          if (cancelled || (token !== undefined && !guard?.isCurrent(token))) {
            return
          }
          setResults(walk.items)
          setNextCursor(walk.nextCursor)
          setMessage(
            walk.errors.length > 0
              ? (walk.errors[0] ?? 'Search unavailable')
              : `${walk.items.length} results${
                  walk.nextCursor ? ' · more available' : ''
                }`,
          )
        })
      },
      // Debounce so typing does not fire a walk per keystroke.
      180,
    )
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [filters])

  // Facet choices: bounded walk of the unfiltered query so kind/owner options
  // are independent of the selected filter page.
  useEffect(() => {
    const guard = guardRef.current
    if (filters.query.trim().length < 2) {
      setFacetsResults([])
      return
    }
    let cancelled = false
    const timer = window.setTimeout(() => {
      const token = guard?.begin()
      void walkSearchPages({
        fetchPage,
        filters: { ...filters, kind: 'all', owner: 'all' },
        maxPages: facetMaxPages,
      }).then((walk) => {
        if (cancelled || (token !== undefined && !guard?.isCurrent(token))) {
          return
        }
        if (walk.errors.length === 0) setFacetsResults(walk.items)
      })
    }, 180)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [filters])

  const loadMore = () => {
    const guard = guardRef.current
    if (!nextCursor || isLoadingMore) return
    const token = guard?.begin()
    setIsLoadingMore(true)
    void walkSearchPages({
      cursor: nextCursor,
      fetchPage,
      filters,
      maxPages: resultMaxPages,
    }).then((walk) => {
      if (token !== undefined && !guard?.isCurrent(token)) return
      setResults((prev) => [...prev, ...walk.items])
      setNextCursor(walk.nextCursor)
      setMessage(
        walk.errors.length > 0
          ? (walk.errors[0] ?? 'Search unavailable')
          : `${results.length + walk.items.length} results${
              walk.nextCursor ? ' · more available' : ''
            }`,
      )
      setIsLoadingMore(false)
    })
  }

  // URL-authoritative filter updates: only non-default values are serialized.
  const updateFilter = (patch: Partial<SearchFilters>) => {
    const params = serializeSearchFilters({ ...filters, ...patch })
    void navigate({
      replace: true,
      search: Object.fromEntries(params),
      to: '/search',
    })
  }

  const kinds = useMemo(
    () => searchFacetChoices(facetsResults).kinds,
    [facetsResults],
  )
  const owners = useMemo(
    () => searchFacetChoices(facetsResults).owners,
    [facetsResults],
  )
  // Filtering happened server-side; the loaded array is already the filtered
  // collection, so no client-side re-filtering that could hide matches.
  const filtered = results

  return (
    <ObservatoryPage
      action={
        <span className="phlo-observatory-pill">{filtered.length} loaded</span>
      }
      description="Search across catalog objects, operational evidence, platform resources, and authored work."
      kicker="Workspace"
      title="Search"
    >
      <section className="phlo-observatory-search-results-surface">
        <div className="phlo-observatory-search-results-toolbar">
          <label className="phlo-observatory-search-field">
            <SearchIcon className="size-4" />
            <input
              aria-label="Search all Observatory resources"
              autoFocus
              onChange={(event) => updateFilter({ query: event.target.value })}
              placeholder="Search datasets, tables, operations, checks, services"
              value={filters.query}
            />
          </label>
          <select
            aria-label="Filter by type"
            onChange={(event) => updateFilter({ kind: event.target.value })}
            value={filters.kind}
          >
            <option value="all">All types</option>
            {kinds.map((entry) => (
              <option key={entry} value={entry}>
                {displayKind(entry)}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by owner"
            onChange={(event) => updateFilter({ owner: event.target.value })}
            value={filters.owner}
          >
            <option value="all">All owners</option>
            {owners.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </div>
        <div className="phlo-observatory-panel-note">{message}</div>
        <div className="phlo-observatory-search-result-list">
          {filtered.map((result) => (
            <Link
              className="phlo-observatory-search-result-row"
              key={`${result.kind}:${result.id}`}
              to={resultHref(result)}
            >
              <span className="phlo-observatory-search-result-kind">
                {displayKind(result.kind)}
              </span>
              <span>
                <strong>{result.label}</strong>
                <small>{result.summary ?? result.id}</small>
              </span>
              <span>
                <small>{resultOwner(result) ?? 'shared'}</small>
              </span>
            </Link>
          ))}
          {filters.query.trim().length >= 2 && filtered.length === 0 && (
            <div className="phlo-observatory-empty-state">
              No resources match the current search and filters.
            </div>
          )}
          {nextCursor !== null && (
            <button
              className="phlo-observatory-load-more"
              disabled={isLoadingMore}
              onClick={loadMore}
              type="button"
            >
              {isLoadingMore
                ? 'Loading more…'
                : `Load more results (${filtered.length} loaded)`}
            </button>
          )}
        </div>
      </section>
    </ObservatoryPage>
  )
}

function resultOwner(result: ObservatorySearchResult): string | null {
  const owner = result.metadata.owner ?? result.metadata.created_by
  return typeof owner === 'string' && owner.trim() ? owner : null
}

function resultHref(result: ObservatorySearchResult): string {
  if (result.kind === 'dataset')
    return `/datasets/${encodeURIComponent(result.id.replace(/^dataset:/, ''))}`
  if (result.kind === 'table')
    return `/tables?tableId=${encodeURIComponent(result.id.replace(/^table:/, ''))}`
  if (result.kind === 'asset')
    return `/lineage?assetId=${encodeURIComponent(result.id.replace(/^asset:/, ''))}`
  if (result.href?.startsWith('/')) return result.href
  return '/'
}

function displayKind(kind: string): string {
  return kind
    .replace(/[_-]+/g, ' ')
    .replace(/^./, (value) => value.toUpperCase())
}
