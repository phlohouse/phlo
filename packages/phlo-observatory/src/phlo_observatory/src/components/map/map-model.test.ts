/**
 * Tests for the lakehouse map model — the flat layout plus the clustered
 * semantic-zoom derivation (group tiles, component sub-clusters, focus
 * neighborhoods, edge aggregation, card cap).
 */
import { describe, expect, test } from 'vitest'

import {
  CARD_CAP,
  COMPONENT_THRESHOLD,
  FLAT_THRESHOLD,
  buildLakehouseMap,
} from './map-model'
import type {
  ObservatoryAsset,
  ObservatoryOperation,
  ObservatoryQualityCheck,
} from '@/observatory/api/types'
import { synthesizeSnapshot } from '@/console/demo'

function asset(
  id: string,
  group: string,
  dependencies: Array<string> = [],
): ObservatoryAsset {
  return {
    id,
    name: id,
    checks: [],
    dependencies,
    group,
    kinds: ['dbt'],
    metadata: {},
    resources: [],
  }
}

const empty = {
  datasets: [],
  operations: [] as Array<ObservatoryOperation>,
  quality: [] as Array<ObservatoryQualityCheck>,
}

describe('buildLakehouseMap — flat mode', () => {
  test('renders cards, no clusters, under the threshold', () => {
    const model = buildLakehouseMap({
      assets: [
        asset('a', 'source'),
        asset('b', 'transform', ['a']),
        asset('c', 'publish', ['b']),
      ],
      ...empty,
    })
    expect(model.clustered).toBe(false)
    expect(model.nodes.map((n) => n.id).sort()).toEqual(['a', 'b', 'c'])
    expect(model.clusters).toEqual([])
    expect(model.edges).toHaveLength(2)
    expect(model.edges[0].weight).toBe(1)
  })

  test('dims non-neighbors of the focused asset', () => {
    const model = buildLakehouseMap({
      assets: [
        asset('a', 'source'),
        asset('b', 'transform', ['a']),
        asset('far', 'other'),
      ],
      focusId: 'b',
      ...empty,
    })
    const byId = new Map(model.nodes.map((n) => [n.id, n]))
    expect(byId.get('a')!.dimmed).toBeFalsy()
    expect(byId.get('far')!.dimmed).toBe(true)
  })
})

describe('buildLakehouseMap — clustered mode', () => {
  // FLAT_THRESHOLD + a few: all in distinct groups so units stay whole.
  const manyAssets = Array.from({ length: FLAT_THRESHOLD + 10 }, (_, i) =>
    asset(`a${i}`, `group_${i % 5}`, i > 0 ? [`a${i - 1}`] : []),
  )

  test('collapses groups into tiles above the threshold', () => {
    const model = buildLakehouseMap({ assets: manyAssets, ...empty })
    expect(model.clustered).toBe(true)
    expect(model.nodes).toEqual([])
    expect(model.clusters).toHaveLength(5)
    expect(model.clusters[0].count).toBeGreaterThan(10)
  })

  test('expanding a group renders its members as cards', () => {
    const model = buildLakehouseMap({
      assets: manyAssets,
      expanded: 'grp:group_1',
      ...empty,
    })
    expect(model.expandedUnit?.id).toBe('grp:group_1')
    expect(model.nodes.every((n) => n.group === 'group_1')).toBe(true)
    expect(model.clusters).toHaveLength(4)
  })

  test('aggregates member edges into weighted cluster edges', () => {
    const model = buildLakehouseMap({ assets: manyAssets, ...empty })
    const total = model.edges.reduce((sum, edge) => sum + edge.weight, 0)
    expect(total).toBe(manyAssets.length - 1)
    expect(
      model.edges.every(
        (edge) =>
          edge.source.startsWith('grp:') || edge.source.startsWith('cmp:'),
      ),
    ).toBe(true)
  })

  test('splits oversized groups into connected components', () => {
    // One giant group fed by two disjoint roots + enough filler elsewhere.
    const half = Math.floor((COMPONENT_THRESHOLD + 10) / 2)
    const big = [
      ...Array.from({ length: COMPONENT_THRESHOLD + 10 }, (_, i) =>
        asset(`x${i}`, 'huge', i >= half ? [`root_b`] : [`root_a`]),
      ),
      asset('root_a', 'source'),
      asset('root_b', 'source'),
      ...Array.from({ length: FLAT_THRESHOLD }, (_, i) =>
        asset(`f${i}`, `fill_${i % 4}`),
      ),
    ]
    const model = buildLakehouseMap({
      assets: big,
      expanded: 'grp:huge',
      ...empty,
    })
    const hugeTiles = model.clusters.filter((c) => c.group === 'huge')
    expect(hugeTiles.length).toBeGreaterThanOrEqual(2)
    expect(hugeTiles.every((c) => c.id.startsWith('cmp:huge:'))).toBe(true)
  })

  test('focusId auto-expands the containing unit', () => {
    const model = buildLakehouseMap({
      assets: manyAssets,
      focusId: 'a7',
      ...empty,
    })
    expect(model.nodes.map((n) => n.id)).toContain('a7')
    expect(model.expandedUnit).not.toBeNull()
  })

  test('caps member cards and reports truncation', () => {
    const giant = [
      ...Array.from({ length: CARD_CAP + 50 }, (_, i) =>
        // One connected chain so it forms a single component.
        asset(`g${i}`, 'giant', i > 0 ? [`g${i - 1}`] : []),
      ),
      ...Array.from({ length: FLAT_THRESHOLD }, (_, i) =>
        asset(`o${i}`, `other_${i % 3}`),
      ),
    ]
    const model = buildLakehouseMap({
      assets: giant,
      expanded: 'cmp:giant:0',
      ...empty,
    })
    expect(model.nodes.length).toBeLessThanOrEqual(CARD_CAP)
    expect(model.truncated).toBe(
      giant.filter((a) => a.group === 'giant').length -
        model.nodes.filter((n) => n.group === 'giant').length,
    )
  })
})

describe('scale: 1000-asset synthetic lakehouse', () => {
  const snapshot = synthesizeSnapshot(1000)
  const assets = snapshot.assets.data ?? []

  test('synthesizes the requested count', () => {
    expect(assets).toHaveLength(1000)
  })

  test('overview renders a small, readable tile set', () => {
    const model = buildLakehouseMap({ assets, ...empty })
    expect(model.clustered).toBe(true)
    const rendered = model.nodes.length + model.clusters.length
    expect(rendered).toBeLessThanOrEqual(60)
    expect(model.edges.length).toBeLessThanOrEqual(200)
  })

  test('builds in well under a second', () => {
    const start = performance.now()
    buildLakehouseMap({ assets, ...empty })
    expect(performance.now() - start).toBeLessThan(500)
  })
})
