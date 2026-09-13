/**
 * Waterline coverage: ops become positioned bars on dataset lanes over a
 * shared 24h window — running bars reach the now-line, publish ops tick,
 * quiet lanes still show when data last landed.
 */
import { describe, expect, it } from 'vitest'

import { buildWaterlineModel } from './waterline-model'
import type {
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryOperation,
} from '@/observatory/api/types'

const NOW = new Date('2026-09-13T12:00:00Z').getTime()
const H = 3_600_000

function asset(id: string, group = 'transform'): ObservatoryAsset {
  return {
    checks: [],
    dependencies: [],
    group,
    id,
    kinds: [],
    metadata: {},
    name: id,
    resources: [],
  }
}

function landed(
  dataset: ObservatoryDataset,
  agoH: number,
): ObservatoryDatasetPipeline {
  return {
    actions: [],
    dataset,
    freshness_at: new Date(NOW - agoH * H).toISOString(),
    freshness_state: 'ok',
    last_run: null,
    stages: [],
  }
}

function dataset(id: string, assetId = `asset_${id}`): ObservatoryDataset {
  return {
    candidate: false,
    classifications: [],
    id,
    kinds: [],
    metadata: {},
    name: id,
    publication_state: 'published',
    readiness_state: 'ok',
    source_refs: [{ id: assetId, kind: 'asset', label: assetId }],
  }
}

function op(
  id: string,
  target: string,
  agoH: number,
  durationMin = 10,
  status: ObservatoryOperation['status'] = 'succeeded',
  kind = 'materialize',
): ObservatoryOperation {
  const started = NOW - agoH * H
  return {
    completed_at:
      status === 'running' || status === 'queued'
        ? null
        : new Date(started + durationMin * 60_000).toISOString(),
    duration_seconds: durationMin * 60,
    health: { state: status === 'failed' ? 'error' : 'ok' },
    id,
    kind,
    metadata: {},
    name: id,
    started_at: new Date(started).toISOString(),
    status,
    target: { id: target, kind: 'asset', label: target },
  }
}

function model(input: {
  assets?: Array<ObservatoryAsset>
  datasets: Array<ObservatoryDataset>
  expanded?: string | null
  focus?: { kind: string; id: string } | null
  operations: Array<ObservatoryOperation>
  pipelines?: Array<ObservatoryDatasetPipeline>
}) {
  return buildWaterlineModel({
    ...input,
    assets: input.assets ?? [],
    pipelines: input.pipelines ?? [],
    now: NOW,
  })
}

describe('buildWaterlineModel', () => {
  it('positions a bar by its start and duration within the window', () => {
    const m = model({
      datasets: [dataset('orders')],
      operations: [op('o1', 'asset_orders', 6, 60)],
    })
    const bar = m.lanes[0].bars[0]
    // 6h ago → left ≈ (24-6+? )/24.96 — started at -6h → frac ≈ 18/24.96
    expect(bar.left).toBeCloseTo(18 / 24.96, 2)
    expect(bar.width).toBeCloseTo(1 / 24.96, 2)
    expect(bar.status).toBe('succeeded')
  })

  it('extends running bars to the window edge', () => {
    const m = model({
      datasets: [dataset('orders')],
      operations: [op('o1', 'asset_orders', 2, 0, 'running')],
    })
    const bar = m.lanes[0].bars[0]
    expect(bar.left + bar.width).toBeGreaterThan(0.95)
  })

  it('marks publish-kind ops as ticks', () => {
    const m = model({
      datasets: [dataset('orders')],
      operations: [
        op('o1', 'asset_orders', 3, 5, 'succeeded', 'dataset.publish'),
      ],
    })
    expect(m.lanes[0].bars[0].publish).toBe(true)
  })

  it('drops ops older than the window', () => {
    const m = model({
      datasets: [dataset('orders')],
      operations: [op('o1', 'asset_orders', 30)],
    })
    expect(m.lanes).toHaveLength(0)
  })

  it('shows lanes with no ops via the landed tick', () => {
    const m = model({
      datasets: [dataset('quiet')],
      operations: [],
      pipelines: [
        {
          actions: [],
          dataset: dataset('quiet'),
          freshness_at: new Date(NOW - 4 * H).toISOString(),
          freshness_state: 'ok',
          last_run: null,
          stages: [],
        },
      ],
    })
    expect(m.lanes).toHaveLength(1)
    expect(m.lanes[0].landedAtFrac).toBeCloseTo(20 / 24.96, 2)
    expect(m.lanes[0].bars).toHaveLength(0)
  })

  it('routes unmapped ops to the platform lane', () => {
    const m = model({
      datasets: [dataset('orders')],
      operations: [op('o1', 'something_else', 1)],
    })
    expect(m.lanes).toHaveLength(0)
    expect(m.platformBars).toHaveLength(1)
  })

  it('orders lanes by most recent activity', () => {
    const m = model({
      datasets: [dataset('old'), dataset('fresh')],
      operations: [
        op('o_old', 'asset_old', 20),
        op('o_fresh', 'asset_fresh', 1),
      ],
    })
    expect(m.lanes.map((l) => l.id)).toEqual(['dataset:fresh', 'dataset:old'])
  })

  it('flags a lane error when any bar failed', () => {
    const m = model({
      datasets: [dataset('orders')],
      operations: [
        op('o_ok', 'asset_orders', 5),
        op('o_bad', 'asset_orders', 2, 10, 'failed'),
      ],
    })
    expect(m.lanes[0].state).toBe('error')
  })

  it('folds dataset lanes into layer lanes beyond the group threshold', () => {
    const datasets = Array.from({ length: 20 }, (_, i) => dataset(`d${i}`))
    const assets = datasets.map((d) => asset(d.source_refs[0].id, 'staging'))
    const m = model({
      assets,
      datasets,
      operations: [op('o1', 'asset_d3', 2)],
      pipelines: datasets.map((d) => landed(d, 5)),
    })
    expect(m.lanes).toHaveLength(1)
    const lane = m.lanes[0]
    expect(lane.kind).toBe('group')
    expect(lane.group).toBe('staging')
    expect(lane.memberCount).toBe(20)
    expect(lane.bars).toHaveLength(1)
  })

  it('expands a layer into member lanes under its header', () => {
    const datasets = Array.from({ length: 20 }, (_, i) => dataset(`d${i}`))
    const assets = datasets.map((d) =>
      asset(d.source_refs[0].id, d.id === 'd0' ? 'publish' : 'staging'),
    )
    const m = model({
      assets,
      datasets,
      expanded: 'staging',
      operations: [op('o1', 'asset_d5', 2)],
      pipelines: datasets.map((d) => landed(d, 5)),
    })
    const header = m.lanes.find((l) => l.id === 'group:staging')
    expect(header?.expanded).toBe(true)
    const members = m.lanes.filter((l) => l.member)
    expect(members).toHaveLength(19)
    // Members sit directly beneath the expanded header.
    const headerIndex = m.lanes.indexOf(header!)
    expect(m.lanes[headerIndex + 1].member).toBe(true)
    // The other layer stays folded.
    expect(m.lanes.some((l) => l.id === 'group:publish')).toBe(true)
  })

  it('expands the layer containing the focused dataset', () => {
    const datasets = Array.from({ length: 20 }, (_, i) => dataset(`d${i}`))
    const assets = datasets.map((d) => asset(d.source_refs[0].id, 'marts'))
    const m = model({
      assets,
      datasets,
      focus: { id: 'd7', kind: 'dataset' },
      operations: [],
      pipelines: datasets.map((d) => landed(d, 5)),
    })
    const header = m.lanes.find((l) => l.id === 'group:marts')
    expect(header?.expanded).toBe(true)
    expect(m.lanes.some((l) => l.member && l.id === 'dataset:d7')).toBe(true)
  })

  it('keeps run lanes individual while datasets group', () => {
    const datasets = Array.from({ length: 20 }, (_, i) => dataset(`d${i}`))
    const assets = datasets.map((d) => asset(d.source_refs[0].id, 'staging'))
    const m = model({
      assets,
      datasets,
      operations: [
        {
          ...op('o_run', 'nope', 1),
          metadata: { scope: 'pipeline-run-3a9cc318ff' },
          target: { id: 'pipeline-run-3a9cc318ff', kind: 'branch', label: 'x' },
        },
      ],
      pipelines: datasets.map((d) => landed(d, 5)),
    })
    expect(m.lanes.some((l) => l.kind === 'run')).toBe(true)
    expect(m.lanes.some((l) => l.id === 'group:staging')).toBe(true)
  })
})
