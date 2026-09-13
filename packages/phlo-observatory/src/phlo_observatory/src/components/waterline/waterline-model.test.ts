/**
 * Waterline coverage: ops become positioned bars on dataset lanes over a
 * shared 24h window — running bars reach the now-line, publish ops tick,
 * quiet lanes still show when data last landed.
 */
import { describe, expect, it } from 'vitest'

import { buildWaterlineModel } from './waterline-model'
import type {
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryOperation,
} from '@/observatory/api/types'

const NOW = new Date('2026-09-13T12:00:00Z').getTime()
const H = 3_600_000

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
  datasets: Array<ObservatoryDataset>
  operations: Array<ObservatoryOperation>
  pipelines?: Array<ObservatoryDatasetPipeline>
}) {
  return buildWaterlineModel({
    ...input,
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
})
