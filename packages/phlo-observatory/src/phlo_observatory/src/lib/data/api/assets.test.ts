/** Verifies typed preview inputs and explicit layer evidence at the asset boundary. */
import { describe, expect, it } from 'vitest'
import {
  assetFreshness,
  assetLayer,
  assetListCursorForEnv,
  assetListPosition,
  assetWriteSummary,
  materializationInputSchema,
  materializationJob,
  nextAssetListPage,
  previewFilterSchema,
  previousAssetListPage,
} from './assets'

describe('Asset read inputs', () => {
  it('retains literal injection text but rejects SQL operators and hidden selectors', () => {
    const filter = {
      column: 'odd"column',
      operator: 'eq',
      value: "O'Brien'; DROP TABLE events; --",
    }
    expect(previewFilterSchema.parse(filter)).toEqual(filter)
    expect(
      previewFilterSchema.safeParse({ ...filter, operator: 'eq OR TRUE' })
        .success,
    ).toBe(false)
    expect(
      previewFilterSchema.safeParse({ ...filter, env: 'prod' }).success,
    ).toBe(false)
    expect(
      previewFilterSchema.safeParse({ ...filter, value: 9007199254740992 })
        .success,
    ).toBe(false)
    expect(
      previewFilterSchema.safeParse({ ...filter, value: Infinity }).success,
    ).toBe(false)
    expect(
      previewFilterSchema.safeParse({
        column: 'site',
        operator: 'is_null',
        value: 'ignored',
      }).success,
    ).toBe(false)
    expect(
      previewFilterSchema.parse({ column: 'site', operator: 'is_null' }),
    ).toEqual({ column: 'site', operator: 'is_null' })
  })

  it('prefers a declared layer without changing the group name', () => {
    const asset = {
      group_name: 'bronze',
      key: ['silver', 'events'],
      layer: 'gold' as const,
    }
    expect(assetLayer(asset)).toBe('gold')
    expect(asset.group_name).toBe('bronze')
    expect(assetLayer({ group_name: 'misc', key: ['silver', 'events'] })).toBe(
      'silver',
    )
    expect(assetLayer({ group_name: 'misc', key: ['events'] })).toBeUndefined()
  })

  it('uses the declared materialization SLA and keeps missing evidence unknown', () => {
    const asset = {
      last_materialization_at: '2026-10-03T00:00:00Z',
      freshness_sla_seconds: 120,
    }
    expect(assetFreshness(asset, Date.parse('2026-10-03T00:02:00Z'))).toBe(
      'fresh',
    )
    expect(assetFreshness(asset, Date.parse('2026-10-03T00:02:00.001Z'))).toBe(
      'stale',
    )
    expect(assetFreshness({ ...asset, freshness_sla_seconds: null })).toBe(
      'unknown',
    )
    expect(assetFreshness({ ...asset, last_materialization_at: null })).toBe(
      'unknown',
    )
    expect(assetFreshness(asset, Date.parse('2026-10-02T23:59:59Z'))).toBe(
      'unknown',
    )
  })

  it('uses explicit server freshness without relabelling snapshots as materializations', () => {
    expect(
      assetFreshness({
        last_materialization_at: null,
        freshness_sla_seconds: 3600,
        freshness_status: 'fresh',
      }),
    ).toBe('fresh')
    expect(
      assetFreshness({
        last_materialization_at: null,
        freshness_sla_seconds: 3600,
        freshness_status: 'unknown',
      }),
    ).toBe('unknown')
  })

  it('keeps asymmetric page freshness evidence instead of marking the whole page unknown', () => {
    const now = Date.parse('2026-10-03T12:00:00Z')
    const observations = [
      {
        last_materialization_at: '2026-10-03T06:44:54.201Z',
        freshness_sla_seconds: 2_678_400,
      },
      { last_materialization_at: null, freshness_sla_seconds: 2_678_400 },
    ]
    expect(observations.map((asset) => assetFreshness(asset, now))).toEqual([
      'fresh',
      'unknown',
    ])
  })

  it('keeps cursor requests environment-scoped and reports loaded page ranges', () => {
    expect(assetListCursorForEnv('opaque-prod-cursor', 'prod', 'prod')).toBe(
      'opaque-prod-cursor',
    )
    expect(assetListCursorForEnv('opaque-prod-cursor', 'prod', 'staging')).toBe(
      undefined,
    )
    expect(assetListPosition(1, 100)).toEqual({
      start: 1,
      end: 100,
      loaded: 100,
    })
    expect(assetListPosition(2, 17)).toEqual({
      start: 101,
      end: 117,
      loaded: 117,
    })
  })

  it('walks asymmetric cursors forward and back across three pages', () => {
    const firstPage = {
      cursor: undefined,
      cursorEnv: undefined,
      previousCursors: [],
    }
    const secondPage = nextAssetListPage(firstPage, 'C1', 'prod')
    const thirdPage = nextAssetListPage(secondPage, 'C2', 'prod')

    expect(thirdPage).toEqual({
      cursor: 'C2',
      cursorEnv: 'prod',
      previousCursors: ['C1'],
    })

    const backToSecond = previousAssetListPage(thirdPage, 'prod')
    expect(backToSecond).toEqual({
      cursor: 'C1',
      cursorEnv: 'prod',
      previousCursors: [],
    })
    expect(
      assetListCursorForEnv(
        backToSecond.cursor,
        backToSecond.cursorEnv,
        'prod',
      ),
    ).toBe('C1')

    const backToFirst = previousAssetListPage(backToSecond, 'prod')
    expect(backToFirst).toEqual({
      cursor: undefined,
      cursorEnv: undefined,
      previousCursors: [],
    })
  })

  it('labels only observed writes and signals truncated or missing history', () => {
    const asset = {
      id: 'dlt_site_directory',
      key: ['dlt_site_directory'],
      description: null,
      compute_kind: null,
      group_name: null,
      is_source: false,
      dependencies: [],
      last_materialization_at: null,
      last_run_id: null,
      relation: null,
      history_scoped: true,
      materializations: [
        {
          run_id: 'run-1',
          timestamp: '2026-10-03T06:44:54.201Z',
          rows_inserted: 3,
          rows_deleted: 3,
        },
      ],
      materialization_history_truncated: true,
    }
    expect(assetWriteSummary(asset)).toBe('≥1 run · +3/−3 rows')
    expect(
      assetWriteSummary({
        ...asset,
        materializations: [],
        materialization_history_truncated: true,
      }),
    ).toBe('History truncated')
    expect(assetWriteSummary({ ...asset, materializations: undefined })).toBe(
      'Unavailable',
    )
    expect(
      assetWriteSummary({
        ...asset,
        materializations: [
          { ...asset.materializations[0], rows_inserted: null },
        ],
        materialization_history_truncated: false,
      }),
    ).toBe('1 run · ?/−3 rows')
  })

  it('resolves a materialization only to its exact available job identity', () => {
    const jobs = [{ id: 'daily_fleet_job' }, { id: 'wap_partition_job' }]

    expect(materializationJob('wap_partition_job', jobs)).toEqual(jobs[1])
    expect(materializationJob('unknown_pipeline', jobs)).toBeUndefined()
    expect(materializationJob(null, jobs)).toBeUndefined()
    expect(materializationJob(undefined, jobs)).toBeUndefined()
  })

  it('allows automatic asset planning without weakening time-range validation', () => {
    const input = {
      env: 'staging',
      id: 'warehouse/orders',
      mode: 'latest',
      write_ref: 'staging-work',
      rebuild_downstream: false,
    }
    expect(materializationInputSchema.parse(input)).toEqual(input)
    expect(
      materializationInputSchema.safeParse({ ...input, job_name: '' }).success,
    ).toBe(false)
    expect(
      materializationInputSchema.safeParse({ ...input, mode: 'backfill' })
        .success,
    ).toBe(false)
    const backfill = {
      ...input,
      job_name: 'configured_daily_job',
      mode: 'backfill',
      from_time: '2026-09-25T00:00:00Z',
      to_time: '2026-09-27T00:00:00Z',
    }
    expect(materializationInputSchema.parse(backfill)).toEqual(backfill)
    expect(
      materializationInputSchema.safeParse({
        ...backfill,
        to_time: backfill.from_time,
      }).success,
    ).toBe(false)
    expect(
      materializationInputSchema.safeParse({ ...backfill, mode: 'latest' })
        .success,
    ).toBe(false)
  })
})
