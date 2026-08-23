/**
 * /search route. Debounced full-text search against the Observatory API;
 * the current query is kept in the URL so results survive a reload.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Search as SearchIcon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { ObservatorySearchResult } from '@/observatory/api/types'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

export const Route = createFileRoute('/search')({ component: SearchResults })

export function SearchResults() {
  const initialQuery =
    typeof window === 'undefined'
      ? ''
      : (new URLSearchParams(window.location.search).get('q') ?? '')
  const [query, setQuery] = useState(initialQuery)
  const [kind, setKind] = useState('all')
  const [owner, setOwner] = useState('all')
  const [results, setResults] = useState<Array<ObservatorySearchResult>>([])
  const [message, setMessage] = useState(
    initialQuery ? 'Searching…' : 'Enter at least two characters.',
  )

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([])
      setMessage('Enter at least two characters.')
      return
    }
    let cancelled = false
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void fetch(
        `/api/observatory/search?q=${encodeURIComponent(query.trim())}`,
        {
          signal: controller.signal,
        },
      )
        .then(async (response) => {
          if (!response.ok)
            throw new Error(`Search unavailable (${response.status})`)
          return response.json() as Promise<{
            items: Array<ObservatorySearchResult>
          }>
        })
        .then((next) => {
          if (cancelled) return
          setResults(next.items)
          setMessage(`${next.items.length} results`)
          const url = new URL(window.location.href)
          url.searchParams.set('q', query.trim())
          window.history.replaceState(null, '', `${url.pathname}${url.search}`)
        })
        .catch((error: unknown) => {
          if (cancelled || controller.signal.aborted) return
          setMessage(
            error instanceof Error ? error.message : 'Search unavailable',
          )
        })
    }, 180)
    return () => {
      cancelled = true
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [query])

  const kinds = useMemo(
    () => Array.from(new Set(results.map((result) => result.kind))).sort(),
    [results],
  )
  const owners = useMemo(
    () =>
      Array.from(
        new Set(
          results
            .map((result) => resultOwner(result))
            .filter((value): value is string => Boolean(value)),
        ),
      ).sort(),
    [results],
  )
  const filtered = useMemo(
    () =>
      results.filter(
        (result) =>
          (kind === 'all' || result.kind === kind) &&
          (owner === 'all' || resultOwner(result) === owner),
      ),
    [kind, owner, results],
  )

  return (
    <ObservatoryPage
      action={
        <span className="phlo-observatory-pill">{filtered.length} visible</span>
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
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search datasets, tables, operations, checks, services"
              value={query}
            />
          </label>
          <select
            aria-label="Filter by type"
            onChange={(event) => setKind(event.target.value)}
            value={kind}
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
            onChange={(event) => setOwner(event.target.value)}
            value={owner}
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
          {query.trim().length >= 2 && filtered.length === 0 && (
            <div className="phlo-observatory-empty-state">
              No resources match the current search and filters.
            </div>
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
