/**
 * Tests lake-model derivation: column grouping, worst-first ordering, check
 * folding, inbound run grouping, and scale caps.
 */
import { describe, expect, it } from 'vitest'

import { buildLakeModel } from './lake-model'
import type {
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryOperation,
  ObservatoryQualityCheck,
} from '@/observatory/api/types'
import { synthesizeSnapshot } from '@/console/demo'

function asset(id: string, group = 'transform'): ObservatoryAsset {
  return {
    checks: [],
    dependencies: [],
    group,
    id,
    kinds: ['dbt'],
    metadata: {},
    name: id,
    resources: [],
  }
}

function dataset(
  id: string,
  sourceId: string,
  overrides: Partial<ObservatoryDataset> = {},
): ObservatoryDataset {
  return {
    candidate: false,
    classifications: [],
    id,
    kinds: ['dbt'],
    metadata: {},
    name: id,
    publication_state: 'published',
    readiness_state: 'ok',
    source_refs: [{ id: sourceId, kind: 'asset', label: sourceId }],
    ...overrides,
  }
}

function op(
  id: string,
  status: ObservatoryOperation['status'],
  targetId?: string,
  scope?: string,
): ObservatoryOperation {
  return {
    health: { state: status === 'failed' ? 'error' : 'ok' },
    id,
    kind: 'materialize',
    metadata: scope ? { scope } : {},
    name: 'materialize',
    started_at: new Date('2025-01-01T10:00:00Z').toISOString(),
    status,
    target: targetId ? { id: targetId, kind: 'asset', label: targetId } : null,
  }
}

const empty = {
  assets: [] as Array<ObservatoryAsset>,
  datasets: [] as Array<ObservatoryDataset>,
  focus: null,
  operations: [] as Array<ObservatoryOperation>,
  pipelines: [] as Array<ObservatoryDatasetPipeline>,
  quality: [] as Array<ObservatoryQualityCheck>,
}

describe('buildLakeModel', () => {
  it('groups datasets into columns by producing asset group', () => {
    const model = buildLakeModel({
      ...empty,
      assets: [asset('a1', 'staging'), asset('a2', 'publish')],
      datasets: [dataset('d1', 'a1'), dataset('d2', 'a2')],
    })
    expect(model.total).toBe(2)
    expect(model.columns.map((c) => c.id)).toEqual(['staging', 'publish'])
    expect(model.columns[0].cells[0].name).toBe('d1')
  })

  it('sorts worst-first within a column', () => {
    const model = buildLakeModel({
      ...empty,
      assets: [asset('a1'), asset('a2'), asset('a3')],
      datasets: [
        dataset('d_ok', 'a1'),
        dataset('d_err', 'a2', { readiness_state: 'error' }),
        dataset('d_warn', 'a3', { readiness_state: 'warning' }),
      ],
    })
    expect(model.columns[0].cells.map((c) => c.name)).toEqual([
      'd_err',
      'd_warn',
      'd_ok',
    ])
    expect(model.columns[0].error).toBe(1)
    expect(model.columns[0].warning).toBe(1)
  })

  it('folds failing checks into cell state', () => {
    const model = buildLakeModel({
      ...empty,
      assets: [asset('a1')],
      datasets: [dataset('d1', 'a1')],
      quality: [
        {
          asset_id: 'a1',
          blocking: true,
          id: 'c1',
          metadata: {},
          name: 'check',
          severity: 'high',
          status: 'failing',
        },
        {
          asset_id: 'a1',
          blocking: false,
          id: 'c2',
          metadata: {},
          name: 'check2',
          severity: 'low',
          status: 'passing',
        },
      ],
    })
    const cell = model.columns[0].cells[0]
    expect(cell.state).toBe('error')
    expect(cell.checksFailing).toBe(1)
    expect(cell.checksTotal).toBe(2)
  })

  it('groups in-flight ops by scope into inbound cards', () => {
    const model = buildLakeModel({
      ...empty,
      assets: [asset('a1'), asset('a2')],
      datasets: [dataset('d1', 'a1'), dataset('d2', 'a2')],
      operations: [
        op('o1', 'running', 'a1', 'pipeline-run-9'),
        op('o2', 'queued', 'a2', 'pipeline-run-9'),
        op('o3', 'running', 'a2', 'pipeline-run-10'),
        op('o4', 'succeeded', 'a1'),
      ],
    })
    expect(model.inbound.length).toBe(2)
    const run9 = model.inbound.find((i) => i.id === 'pipeline-run-9')!
    expect(run9.total).toBe(2)
    expect(run9.label).toBe('run 9')
    expect(run9.targets).toContain('a1')
    // d1 gets one inbound op via its source asset
    expect(model.columns[0].cells.find((c) => c.id === 'd1')?.inbound).toBe(1)
  })

  it('drops finished ops from inbound but keeps the dataset clean', () => {
    const model = buildLakeModel({
      ...empty,
      assets: [asset('a1')],
      datasets: [dataset('d1', 'a1')],
      operations: [op('o1', 'succeeded', 'a1'), op('o2', 'failed', 'a2')],
    })
    expect(model.inbound).toEqual([])
    expect(model.columns[0].cells[0].inbound).toBe(0)
  })

  it('marks the focused dataset', () => {
    const model = buildLakeModel({
      ...empty,
      assets: [asset('a1'), asset('a2')],
      datasets: [dataset('d1', 'a1'), dataset('d2', 'a2')],
      focus: { kind: 'dataset', id: 'd2' },
    })
    expect(model.columns[0].cells.find((c) => c.id === 'd2')?.focused).toBe(
      true,
    )
    expect(model.columns[0].cells.find((c) => c.id === 'd1')?.focused).toBe(
      false,
    )
  })

  it('renders a dense lake at scale without unbounded cells', () => {
    const snapshot = synthesizeSnapshot(1000)
    const model = buildLakeModel({
      assets: snapshot.assets.data!,
      datasets: snapshot.datasets.data!,
      focus: null,
      operations: snapshot.operations.data!,
      pipelines: snapshot.pipelines.data!,
      quality: snapshot.quality.data!,
    })
    expect(model.total).toBeGreaterThan(200)
    for (const column of model.columns) {
      expect(column.cells.length).toBeLessThanOrEqual(120)
    }
    expect(model.inbound.length).toBeGreaterThan(0)
  })
})
