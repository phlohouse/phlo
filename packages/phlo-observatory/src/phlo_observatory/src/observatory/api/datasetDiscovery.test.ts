/**
 * Tests Dataset discovery client helpers: bounded next_cursor traversal,
 * URL filter round-trips, stale-page guards, and search facet choices.
 */
import { describe, expect, it } from 'vitest'

import {
  createRequestGuard,
  datasetPageQuery,
  defaultDatasetFilters,
  parseDatasetFilters,
  parseSearchFilters,
  searchFacetChoices,
  searchPageQuery,
  serializeDatasetFilters,
  serializeSearchFilters,
  walkDatasetPages,
  walkSearchPages,
} from './datasetDiscovery'
import type { ObservatoryDataset } from './types'

function dataset(id: string): ObservatoryDataset {
  return {
    id,
    name: id,
    classifications: [],
    publication_state: 'published',
    readiness_state: 'ok',
    candidate: false,
    kinds: ['table'],
    source_refs: [],
    metadata: {},
  }
}

describe('walkDatasetPages', () => {
  it('consumes next_cursor across pages until exhaustion', async () => {
    const pages = [
      { items: [dataset('a')], nextCursor: 'c2' },
      { items: [dataset('b')], nextCursor: 'c3' },
      { items: [dataset('c')], nextCursor: null },
    ]
    const seen: Array<string | null> = []
    const walk = await walkDatasetPages({
      fetchPage: async ({ cursor, filters, limit }) => {
        seen.push(cursor)
        expect(filters).toEqual(defaultDatasetFilters)
        expect(limit).toBe(100)
        return pages[seen.length - 1] ?? { items: [], nextCursor: null }
      },
      filters: defaultDatasetFilters,
    })

    expect(seen).toEqual([null, 'c2', 'c3'])
    expect(walk.items.map((item) => item.id)).toEqual(['a', 'b', 'c'])
    expect(walk.nextCursor).toBeNull()
    expect(walk.pagesLoaded).toBe(3)
    expect(walk.truncated).toBe(false)
    expect(walk.errors).toEqual([])
  })

  it('stops at the page cap while preserving the remaining cursor', async () => {
    const cursors: Array<string | null> = []
    const walk = await walkDatasetPages({
      fetchPage: async ({ cursor }) => {
        cursors.push(cursor)
        return {
          items: [dataset(`page-${cursors.length}`)],
          nextCursor: `next-${cursors.length}`,
        }
      },
      filters: defaultDatasetFilters,
      maxPages: 2,
    })

    expect(cursors).toEqual([null, 'next-1'])
    expect(walk.pagesLoaded).toBe(2)
    expect(walk.truncated).toBe(true)
    expect(walk.nextCursor).toBe('next-2')
  })

  it('does not claim exhaustion when a page fails', async () => {
    const walk = await walkDatasetPages({
      fetchPage: async ({ cursor }) =>
        cursor === null
          ? { items: [dataset('a')], nextCursor: 'c2' }
          : { items: [], nextCursor: null, error: 'phlo-api error: 503' },
      filters: defaultDatasetFilters,
      maxPages: 3,
    })

    expect(walk.items.map((item) => item.id)).toEqual(['a'])
    expect(walk.errors).toEqual(['phlo-api error: 503'])
    // The failed page's cursor stays available for an explicit retry.
    expect(walk.nextCursor).toBe('c2')
    expect(walk.truncated).toBe(true)
  })
})

describe('walkSearchPages', () => {
  it('supplies the same filters with every page request', async () => {
    const filters = { query: 'orders', kind: 'dataset', owner: 'all' }
    const seenFilters: Array<typeof filters> = []
    const walk = await walkSearchPages({
      fetchPage: async ({ filters: pageFilters }) => {
        seenFilters.push(pageFilters)
        return { items: [], nextCursor: null }
      },
      filters,
      maxPages: 1,
    })

    expect(seenFilters).toEqual([filters])
    expect(walk.truncated).toBe(false)
  })
})

describe('dataset filter URL round-trip', () => {
  it('is the identity through serialize and parse', () => {
    const filters = {
      query: 'gold orders',
      owner: 'data-platform',
      classification: 'internal',
      publicationState: 'published',
      readinessState: 'warning',
      candidate: 'false' as const,
    }
    const params = serializeDatasetFilters(filters)
    expect(params.toString()).toBe(
      'q=gold+orders&owner=data-platform&classification=internal&publicationState=published&readinessState=warning&candidate=false',
    )
    expect(parseDatasetFilters(params)).toEqual(filters)
  })

  it('omits default values so the URL only describes the active view', () => {
    const params = serializeDatasetFilters(defaultDatasetFilters)
    expect(params.toString()).toBe('')
    expect(parseDatasetFilters(new URLSearchParams(''))).toEqual(
      defaultDatasetFilters,
    )
  })

  it('round-trips a restored URL', () => {
    const url = new URL(
      'https://observatory.example.test/datasets?q=orders&owner=data-platform&candidate=true',
    )
    const filters = parseDatasetFilters(url.searchParams)
    expect(serializeDatasetFilters(filters).toString()).toBe(
      'q=orders&owner=data-platform&candidate=true',
    )
  })

  it('maps candidate to a strict boolean filter', () => {
    expect(
      parseDatasetFilters(new URLSearchParams('candidate=yes')).candidate,
    ).toBe('all')
    expect(
      parseDatasetFilters(new URLSearchParams('candidate=false')).candidate,
    ).toBe('false')
  })

  it('builds page queries with filters and limit supplied every page', () => {
    expect(
      datasetPageQuery(
        { ...defaultDatasetFilters, query: 'orders', candidate: 'true' },
        100,
        null,
      ),
    ).toBe('?q=orders&candidate=true&limit=100')
    expect(datasetPageQuery(defaultDatasetFilters, 100, 'cursor-42')).toBe(
      '?limit=100&cursor=cursor-42',
    )
  })
})

describe('search filter URL round-trip', () => {
  it('is the identity through serialize and parse', () => {
    const filters = { query: 'churn', kind: 'dataset', owner: 'growth' }
    const params = serializeSearchFilters(filters)
    expect(params.toString()).toBe('q=churn&kind=dataset&owner=growth')
    expect(parseSearchFilters(params)).toEqual(filters)
  })

  it('omits default values', () => {
    expect(
      serializeSearchFilters({
        query: '',
        kind: 'all',
        owner: 'all',
      }).toString(),
    ).toBe('')
  })

  it('builds page queries with q, kind, owner, limit, and cursor', () => {
    expect(
      searchPageQuery(
        { query: 'orders', kind: 'dataset', owner: 'growth' },
        100,
        'c9',
      ),
    ).toBe('?q=orders&kind=dataset&owner=growth&limit=100&cursor=c9')
  })
})

describe('createRequestGuard', () => {
  it('invalidates earlier request tokens when a newer one begins', () => {
    const guard = createRequestGuard()
    const first = guard.begin()
    expect(guard.isCurrent(first)).toBe(true)

    const second = guard.begin()
    expect(guard.isCurrent(first)).toBe(false)
    expect(guard.isCurrent(second)).toBe(true)
  })
})

describe('searchFacetChoices', () => {
  it('derives sorted kinds and owners from result metadata', () => {
    const choices = searchFacetChoices([
      {
        id: 'dataset:a',
        label: 'a',
        kind: 'dataset',
        metadata: { owner: 'data-platform' },
      },
      {
        id: 'table:b',
        label: 'b',
        kind: 'table',
        metadata: { owner: 'growth' },
      },
      {
        id: 'table:c',
        label: 'c',
        kind: 'table',
        metadata: { created_by: 'analytics' },
      },
    ])

    expect(choices.kinds).toEqual(['dataset', 'table'])
    expect(choices.owners).toEqual(['analytics', 'data-platform', 'growth'])
  })
})
