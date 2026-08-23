/**
 * Tests plugin registry fetch caching and resolution of installed versus
 * available plugin lists.
 */
import { describe, expect, it, vi } from 'vitest'

import {
  createRegistryFetcherWithCache,
  resolvePluginLists,
} from './plugins.server'
import type { PluginInfo } from './plugins.server'

function plugin(
  partial: Partial<PluginInfo> & Pick<PluginInfo, 'name' | 'type' | 'version'>,
): PluginInfo {
  return {
    name: partial.name,
    type: partial.type,
    version: partial.version,
    description: partial.description,
    author: partial.author,
    homepage: partial.homepage,
    tags: partial.tags,
    installed: partial.installed,
    verified: partial.verified,
    core: partial.core,
    package: partial.package,
    category: partial.category,
    profile: partial.profile,
    default: partial.default,
  }
}

describe('plugins.server resolvePluginLists', () => {
  it('uses CLI result when available plugins are present', async () => {
    const runPluginListCommand = vi.fn().mockResolvedValue(
      JSON.stringify({
        installed: [
          plugin({
            name: 'phlo-observatory',
            type: 'service',
            version: '0.1.0',
          }),
        ],
        available: [
          plugin({ name: 'phlo-dlt', type: 'source', version: '0.1.0' }),
        ],
      }),
    )
    const fetchRegistry = vi.fn()

    const result = await resolvePluginLists({
      runPluginListCommand,
      fetchRegistry,
    })

    expect(result.installed).toHaveLength(1)
    expect(result.available).toHaveLength(1)
    expect(fetchRegistry).not.toHaveBeenCalled()
  })

  it('fills available plugins from registry when CLI returns installed only', async () => {
    const runPluginListCommand = vi.fn().mockResolvedValue(
      JSON.stringify({
        installed: [
          plugin({
            name: 'phlo-observatory',
            type: 'service',
            version: '0.1.0',
          }),
        ],
        available: [],
      }),
    )
    const fetchRegistry = vi
      .fn()
      .mockResolvedValue([
        plugin({ name: 'phlo-dbt', type: 'transform', version: '0.1.0' }),
      ])

    const result = await resolvePluginLists({
      runPluginListCommand,
      fetchRegistry,
    })

    expect(result.installed).toHaveLength(1)
    expect(result.available).toHaveLength(1)
    expect(fetchRegistry).toHaveBeenCalledTimes(1)
  })

  it('falls back to registry when CLI command fails', async () => {
    const runPluginListCommand = vi
      .fn()
      .mockRejectedValue(new Error('phlo not found'))
    const fetchRegistry = vi
      .fn()
      .mockResolvedValue([
        plugin({ name: 'phlo-pandera', type: 'quality', version: '0.1.0' }),
      ])

    const result = await resolvePluginLists({
      runPluginListCommand,
      fetchRegistry,
    })

    expect(result.installed).toEqual([])
    expect(result.available).toHaveLength(1)
    expect(fetchRegistry).toHaveBeenCalledTimes(1)
  })

  it('returns empty lists when both CLI and registry fail', async () => {
    const runPluginListCommand = vi
      .fn()
      .mockRejectedValue(new Error('phlo not found'))
    const fetchRegistry = vi
      .fn()
      .mockRejectedValue(new Error('registry unavailable'))

    const result = await resolvePluginLists({
      runPluginListCommand,
      fetchRegistry,
    })

    expect(result.installed).toEqual([])
    expect(result.available).toEqual([])
  })
})

describe('plugins.server createRegistryFetcherWithCache', () => {
  it('returns cached registry data within ttl', async () => {
    let nowMs = 1_000
    const fetchRegistry = vi
      .fn()
      .mockResolvedValue([
        plugin({ name: 'phlo-dbt', type: 'transform', version: '0.1.1' }),
      ])
    const fetchWithCache = createRegistryFetcherWithCache({
      fetchRegistry,
      ttlMs: 300_000,
      staleOnErrorMs: 86_400_000,
      now: () => nowMs,
    })

    const first = await fetchWithCache()
    nowMs += 30_000
    const second = await fetchWithCache()

    expect(first).toEqual(second)
    expect(fetchRegistry).toHaveBeenCalledTimes(1)
  })

  it('refreshes registry data after ttl expiry', async () => {
    let nowMs = 1_000
    const fetchRegistry = vi
      .fn()
      .mockResolvedValueOnce([
        plugin({ name: 'phlo-dbt', type: 'transform', version: '0.1.1' }),
      ])
      .mockResolvedValueOnce([
        plugin({ name: 'phlo-dbt', type: 'transform', version: '0.1.2' }),
      ])
    const fetchWithCache = createRegistryFetcherWithCache({
      fetchRegistry,
      ttlMs: 60_000,
      staleOnErrorMs: 86_400_000,
      now: () => nowMs,
    })

    const first = await fetchWithCache()
    nowMs += 61_000
    const second = await fetchWithCache()

    expect(first[0].version).toBe('0.1.1')
    expect(second[0].version).toBe('0.1.2')
    expect(fetchRegistry).toHaveBeenCalledTimes(2)
  })

  it('serves stale cache when refresh fails inside stale window', async () => {
    let nowMs = 1_000
    const fetchRegistry = vi
      .fn()
      .mockResolvedValueOnce([
        plugin({ name: 'phlo-dlt', type: 'source', version: '0.1.1' }),
      ])
      .mockRejectedValueOnce(new Error('network down'))
    const fetchWithCache = createRegistryFetcherWithCache({
      fetchRegistry,
      ttlMs: 60_000,
      staleOnErrorMs: 86_400_000,
      now: () => nowMs,
    })

    const first = await fetchWithCache()
    nowMs += 61_000
    const second = await fetchWithCache()

    expect(first).toEqual(second)
    expect(fetchRegistry).toHaveBeenCalledTimes(2)
  })

  it('throws when stale cache is older than stale-on-error window', async () => {
    let nowMs = 1_000
    const fetchRegistry = vi
      .fn()
      .mockResolvedValueOnce([
        plugin({ name: 'phlo-dlt', type: 'source', version: '0.1.1' }),
      ])
      .mockRejectedValueOnce(new Error('registry unavailable'))
    const fetchWithCache = createRegistryFetcherWithCache({
      fetchRegistry,
      ttlMs: 60_000,
      staleOnErrorMs: 120_000,
      now: () => nowMs,
    })

    await fetchWithCache()
    nowMs += 200_000

    await expect(fetchWithCache()).rejects.toThrow('registry unavailable')
  })

  it('deduplicates concurrent refreshes with single in-flight request', async () => {
    const nowMs = 1_000
    let releaseFetch: () => void = () => {
      throw new Error('Expected fetch release callback to be set')
    }
    const fetchRegistry = vi.fn().mockImplementation(
      () =>
        new Promise<Array<PluginInfo>>((resolve) => {
          releaseFetch = () =>
            resolve([
              plugin({
                name: 'phlo-pandera',
                type: 'quality',
                version: '0.1.1',
              }),
            ])
        }),
    )
    const fetchWithCache = createRegistryFetcherWithCache({
      fetchRegistry,
      ttlMs: 60_000,
      staleOnErrorMs: 86_400_000,
      now: () => nowMs,
    })

    const first = fetchWithCache()
    const second = fetchWithCache()
    expect(fetchRegistry).toHaveBeenCalledTimes(1)
    releaseFetch()

    const [a, b] = await Promise.all([first, second])
    expect(a).toEqual(b)
  })
})
