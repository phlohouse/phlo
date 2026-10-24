/** Verifies asset table sorting preserves zero and orders unavailable metadata last. */
import { describe, expect, it } from 'vitest'
import {
  constructTable,
  createSortedRowModel,
  rowSortingFeature,
  sortFn_text,
  tableFeatures,
} from '@tanstack/react-table'
import { storeReactivityBindings } from '@tanstack/table-core/store-reactivity-bindings'
import {
  assetNumericSortValue,
  assetTableColumns,
  assetTimestampSortValue,
  formatAssetTimestamp,
} from './assets-table'
import type { ApiAsset } from './api/assets'

function makeAsset(
  id: string,
  values: Pick<ApiAsset, 'row_count' | 'last_materialization_at'> &
    Pick<ApiAsset, 'size_bytes'>,
): ApiAsset {
  return {
    id,
    key: [id],
    description: null,
    compute_kind: null,
    group_name: null,
    is_source: false,
    dependencies: [],
    last_materialization_at: values.last_materialization_at ?? null,
    last_run_id: null,
    relation: null,
    history_scoped: true,
    row_count: values.row_count ?? null,
    size_bytes: values.size_bytes ?? null,
  }
}

function sortedIds(data: Array<ApiAsset>, id: string, desc = false) {
  const features = tableFeatures({
    coreReactivityFeature: storeReactivityBindings(),
    rowSortingFeature,
    sortedRowModel: createSortedRowModel(),
    sortFns: { text: sortFn_text },
  })
  const table = constructTable({
    features,
    columns: assetTableColumns,
    data,
    getRowId: (asset) => asset.id,
    state: { sorting: [{ id, desc }] },
  })
  return table.getRowModel().rows.map((row) => row.original.id)
}

describe('asset table sort values', () => {
  it('keeps zero distinct from unavailable numeric metadata', () => {
    expect(assetNumericSortValue(0)).toBe(0)
    expect(assetNumericSortValue(24)).toBe(24)
    expect(assetNumericSortValue(null)).toBeUndefined()
    expect(assetNumericSortValue(undefined)).toBeUndefined()
  })

  it('orders asymmetric row counts numerically and keeps unavailable values last in both directions', () => {
    const rows = [
      makeAsset('twelve', {
        row_count: 12,
        last_materialization_at: null,
        size_bytes: 12,
      }),
      makeAsset('unknown', {
        row_count: null,
        last_materialization_at: null,
        size_bytes: null,
      }),
      makeAsset('two', {
        row_count: 2,
        last_materialization_at: null,
        size_bytes: 2,
      }),
      makeAsset('zero', {
        row_count: 0,
        last_materialization_at: null,
        size_bytes: 0,
      }),
    ]
    expect(sortedIds(rows, 'rowCount')).toEqual([
      'zero',
      'two',
      'twelve',
      'unknown',
    ])
    expect(sortedIds(rows, 'rowCount', true)).toEqual([
      'twelve',
      'two',
      'zero',
      'unknown',
    ])
    expect(sortedIds(rows, 'sizeBytes')).toEqual([
      'zero',
      'two',
      'twelve',
      'unknown',
    ])
  })

  it('sorts timestamps by their actual instant, including different offsets', () => {
    const earlier = assetTimestampSortValue('2026-01-01T09:30:00-05:00')
    const later = assetTimestampSortValue('2026-01-01T15:00:00Z')
    expect(earlier).toBeDefined()
    expect(later).toBeDefined()
    expect(earlier).toBeLessThan(later!)
  })

  it('orders materialization dates by instant and keeps missing dates last', () => {
    const rows = [
      makeAsset('missing', { row_count: null, last_materialization_at: null }),
      makeAsset('later', {
        row_count: null,
        last_materialization_at: '2026-01-01T15:00:00Z',
      }),
      makeAsset('earlier', {
        row_count: null,
        last_materialization_at: '2026-01-01T09:30:00-05:00',
      }),
    ]
    expect(sortedIds(rows, 'lastMaterialized')).toEqual([
      'earlier',
      'later',
      'missing',
    ])
  })

  it('keeps absent and invalid timestamps unavailable and formats valid dates readably', () => {
    expect(assetTimestampSortValue(null)).toBeUndefined()
    expect(assetTimestampSortValue('not-a-timestamp')).toBeUndefined()
    expect(formatAssetTimestamp(null)).toBe('Not observed')
    expect(formatAssetTimestamp('not-a-timestamp')).toBe('Invalid timestamp')
    expect(formatAssetTimestamp('2026-01-01T10:00:00-05:00')).toBe(
      '1 Jan 2026, 15:00 UTC',
    )
  })
})

describe('compact inventory evidence', () => {
  it('uses human-sized counts and decimal byte units without losing zero', async () => {
    const { formatInventoryCount, formatInventoryBytes } =
      await import('./assets-table')
    expect(formatInventoryCount(0)).toBe('0')
    expect(formatInventoryCount(1_210_000_000)).toBe('1.2B')
    expect(formatInventoryBytes(0)).toBe('0 B')
    expect(formatInventoryBytes(1_400_000_000)).toBe('1.4 GB')
  })

  it('keeps missing days neutral and excludes observations outside the seven-day window', async () => {
    const { assetMaterializationDays } = await import('./assets-table')
    const row = makeAsset('bronze.events', {
      row_count: 0,
      last_materialization_at: null,
    })
    row.materializations = [
      {
        run_id: 'past',
        timestamp: '2026-10-14T12:00:00Z',
        rows_inserted: 10,
        rows_deleted: 0,
      },
      {
        run_id: 'one',
        timestamp: '2026-10-21T12:00:00Z',
        rows_inserted: 0,
        rows_deleted: 0,
      },
      {
        run_id: 'two',
        timestamp: '2026-10-21T13:00:00Z',
        rows_inserted: 8,
        rows_deleted: 0,
      },
      {
        run_id: 'future',
        timestamp: '2026-10-22T18:00:00Z',
        rows_inserted: 8,
        rows_deleted: 0,
      },
    ]
    const days = assetMaterializationDays(
      row,
      Date.parse('2026-10-22T15:00:00Z'),
    )
    expect(days).toHaveLength(7)
    expect(days[0].date).toBe('2026-10-16')
    expect(days.map((day) => day.count)).toEqual([0, 0, 0, 0, 0, 2, 0])
    expect(
      assetMaterializationDays(
        { ...row, materializations: undefined },
        Date.parse('2026-10-22T15:00:00Z'),
      ).every((day) => day.count === 0),
    ).toBe(true)
  })
})
