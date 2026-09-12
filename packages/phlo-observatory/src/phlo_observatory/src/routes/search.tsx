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
import { ChevronRight, Search as SearchIcon } from 'lucide-react'
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
import { Page, PageHeader } from '@/components/observatory/page'
import { EmptyBlock } from '@/components/observatory/states'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

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
    <Page>
      <PageHeader
        actions={<Badge variant="secondary">{filtered.length} loaded</Badge>}
        description="Search across catalog objects, operational evidence, platform resources, and authored work."
        title="Search"
      />
      <SectionCard>
        <div className="flex flex-wrap items-center gap-2 border-b p-2">
          <div className="relative min-w-56 flex-1">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
            <Input
              aria-label="Search all Observatory resources"
              autoFocus
              className="pl-7"
              onChange={(event) => updateFilter({ query: event.target.value })}
              placeholder="Search datasets, tables, operations, checks, services"
              value={filters.query}
            />
          </div>
          <Select
            onValueChange={(value) => updateFilter({ kind: value ?? 'all' })}
            value={filters.kind}
          >
            <SelectTrigger aria-label="Filter by type" className="w-40">
              <SelectValue placeholder="All types" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              {kinds.map((entry) => (
                <SelectItem key={entry} value={entry}>
                  {displayKind(entry)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            onValueChange={(value) => updateFilter({ owner: value ?? 'all' })}
            value={filters.owner}
          >
            <SelectTrigger aria-label="Filter by owner" className="w-40">
              <SelectValue placeholder="All owners" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All owners</SelectItem>
              {owners.map((entry) => (
                <SelectItem key={entry} value={entry}>
                  {entry}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="text-muted-foreground border-b px-3 py-1.5 font-mono text-[10px]">
          {message}
        </div>
        <div className="divide-y divide-border">
          {filtered.map((result) => (
            <Link
              className="hover:bg-accent/50 flex items-center gap-3 px-3 py-2 transition-colors"
              key={`${result.kind}:${result.id}`}
              to={resultHref(result)}
            >
              <Badge
                className="w-24 flex-none justify-center font-mono text-[10px]"
                variant="secondary"
              >
                {displayKind(result.kind)}
              </Badge>
              <span className="min-w-0 flex-1">
                <span className="text-foreground block truncate text-xs font-medium">
                  {result.label}
                </span>
                <span className="text-muted-foreground block truncate text-[11px]">
                  {result.summary ?? result.id}
                </span>
              </span>
              <span className="text-muted-foreground hidden font-mono text-[10px] sm:inline">
                {resultOwner(result) ?? 'shared'}
              </span>
              <ChevronRight className="text-muted-foreground size-3.5" />
            </Link>
          ))}
          {filters.query.trim().length >= 2 && filtered.length === 0 && (
            <EmptyBlock
              description="No resources match the current search and filters."
              title="No matches"
            />
          )}
          {nextCursor !== null && (
            <div className="p-2">
              <Button
                className="w-full"
                disabled={isLoadingMore}
                onClick={loadMore}
                size="sm"
                variant="outline"
              >
                {isLoadingMore
                  ? 'Loading more…'
                  : `Load more results (${filtered.length} loaded)`}
              </Button>
            </div>
          )}
        </div>
      </SectionCard>
    </Page>
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
