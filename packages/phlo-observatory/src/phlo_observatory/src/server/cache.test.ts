/**
 * Tests the metadata cache: key building, TTLs, invalidation, and stats
 * accounting.
 */
import { beforeEach, describe, expect, it } from 'vitest'

import {
  cacheKeys,
  cacheTTL,
  clearCache,
  getCacheStats,
  invalidateCache,
  withCache,
} from './cache'

function mockFn<T>(getValue: () => T) {
  let callCount = 0
  return {
    fn: () => {
      callCount++
      return Promise.resolve(getValue())
    },
    getCallCount: () => callCount,
  }
}

describe('MetadataCache', () => {
  beforeEach(() => {
    clearCache()
  })

  describe('withCache', () => {
    it('caches successful results', async () => {
      const mock = mockFn(() => ({ data: 'test' }))

      const result1 = await withCache(mock.fn, 'test:key', 60000).then(
        async (cachedResult) => {
          const result2 = await withCache(mock.fn, 'test:key', 60000)
          return [cachedResult, result2] as const
        },
      )
      const result2 = result1[1]

      expect(result1[0]).toEqual({ data: 'test' })
      expect(result2).toEqual({ data: 'test' })
      expect(mock.getCallCount()).toBe(1)
    })

    it('does not cache error results', async () => {
      const mock = mockFn(() => ({ error: 'failed' }))

      await withCache(mock.fn, 'test:error', 60000)
      await withCache(mock.fn, 'test:error', 60000)

      expect(mock.getCallCount()).toBe(2)
    })

    it('respects different cache keys', async () => {
      let callCount = 0
      const fn = () => {
        callCount++
        return Promise.resolve({ data: callCount })
      }

      const [result1, result2] = await Promise.all([
        withCache(fn, 'test:key1', 60000),
        withCache(fn, 'test:key2', 60000),
      ])

      expect(result1).toEqual({ data: 1 })
      expect(result2).toEqual({ data: 2 })
      expect(callCount).toBe(2)
    })
  })

  describe('getCacheStats', () => {
    it('tracks hits and misses', async () => {
      const mock = mockFn(() => ({ data: 'test' }))

      await withCache(mock.fn, 'stats:test', 60000)
      await Promise.all([
        withCache(mock.fn, 'stats:test', 60000),
        withCache(mock.fn, 'stats:other', 60000),
      ])

      const stats = getCacheStats()
      expect(stats.hits).toBe(1)
      expect(stats.misses).toBe(2)
      expect(stats.entries).toBe(2)
      expect(stats.hitRate).toBeCloseTo(1 / 3)
    })

    it('groups entries by prefix', async () => {
      const mock = mockFn(() => ({ data: 'test' }))

      await Promise.all([
        withCache(mock.fn, 'iceberg:tables:cat:main', 60000),
        withCache(mock.fn, 'iceberg:schema:cat:s:t', 60000),
        withCache(mock.fn, 'dagster:assets:url', 60000),
      ])

      const stats = getCacheStats()
      expect(stats.entriesByPrefix).toEqual({
        iceberg: 2,
        dagster: 1,
      })
    })
  })

  describe('invalidateCache', () => {
    it('invalidates single key', async () => {
      const mock = mockFn(() => ({ data: 'test' }))
      await withCache(mock.fn, 'inv:single', 60000)

      const count = invalidateCache('inv:single')
      expect(count).toBe(1)

      const stats = getCacheStats()
      expect(stats.entries).toBe(0)
    })

    it('invalidates by pattern', async () => {
      const mock = mockFn(() => ({ data: 'test' }))
      await withCache(mock.fn, 'pattern:a:1', 60000)
      await withCache(mock.fn, 'pattern:a:2', 60000)
      await withCache(mock.fn, 'pattern:b:1', 60000)

      const count = invalidateCache('pattern:a:*')
      expect(count).toBe(2)

      const stats = getCacheStats()
      expect(stats.entries).toBe(1)
    })
  })

  describe('clearCache', () => {
    it('removes all entries', async () => {
      const mock = mockFn(() => ({ data: 'test' }))
      await withCache(mock.fn, 'clear:1', 60000)
      await withCache(mock.fn, 'clear:2', 60000)

      clearCache()

      const stats = getCacheStats()
      expect(stats.entries).toBe(0)
    })
  })

  describe('cacheKeys', () => {
    it('generates consistent table keys', () => {
      const key = cacheKeys.tables('main')
      expect(key).toBe('iceberg:tables:main')
    })

    it('generates consistent schema keys', () => {
      const key = cacheKeys.tableSchema('iceberg', 'gold', 'fct_orders')
      expect(key).toBe('iceberg:schema:iceberg:gold:fct_orders')
    })

    it('generates consistent asset keys', () => {
      const key = cacheKeys.assets('http://localhost:3000/graphql')
      expect(key).toContain('dagster:assets:')
    })

    it('generates consistent search index keys', () => {
      const key = cacheKeys.searchIndex(
        'http://localhost:3000/graphql',
        'http://localhost:8080',
      )
      expect(key).toContain('search:index:')
    })
  })

  describe('cacheTTL', () => {
    it('has expected TTL values', () => {
      expect(cacheTTL.tables).toBe(5 * 60 * 1000)
      expect(cacheTTL.tableSchema).toBe(10 * 60 * 1000)
      expect(cacheTTL.assets).toBe(2 * 60 * 1000)
      expect(cacheTTL.searchIndex).toBe(5 * 60 * 1000)
    })
  })
})
