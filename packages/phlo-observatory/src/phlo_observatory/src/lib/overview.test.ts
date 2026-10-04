/** Verifies combined overview service filters, including unobserved services. */
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import type { ApiRun } from '@/lib/data/api/pipelines'
import { filterServices, runBuckets, runsInRange } from '@/routes/_app/index'
import { HealthBar } from '@/components/phlo/status'
import { numericColumns } from '@/components/query/result-views'
import { assetLayer } from '@/lib/data/api/assets'
import { AuditsTab, SnapshotsTab } from '@/components/assets/asset-tabs'
import { observedRunState } from '@/routes/_app/pipelines/index'

const services = [
  {
    id: 'Asset API',
    status: 'healthy',
    observed_at: '2026-10-02T10:00:00Z',
    response_time_seconds: 0.12,
  },
  {
    id: 'Run Worker',
    status: 'unknown',
    observed_at: null,
    response_time_seconds: null,
  },
] satisfies Parameters<typeof filterServices>[0]

describe('overview service filters', () => {
  it('combines trimmed, case-insensitive names with exact statuses', () => {
    expect(filterServices(services, '  RUN ', 'unknown')).toEqual([services[1]])
    expect(filterServices(services, 'run', 'healthy')).toEqual([])
  })

  it('keeps unobserved entries when filters are cleared', () => {
    expect(filterServices(services, '', '')).toEqual(services)
    expect(filterServices(services, '', 'healthy')).toEqual([services[0]])
  })
})

describe('dashboard observations', () => {
  const observedAt = '2026-10-02T12:00:00Z'
  const run = (created_at: string, status: ApiRun['status']): ApiRun => ({
    run_id: created_at,
    job_id: 'example',
    status,
    created_at,
    started_at: null,
    ended_at: null,
    duration_seconds: null,
    selected_assets: [],
  })
  const runs = [
    run('2026-10-01T11:59:59Z', 'SUCCESS'),
    run('2026-10-01T12:00:00Z', 'FAILURE'),
    run('2026-10-01T14:30:00Z', 'SUCCESS'),
    run('2026-10-02T12:00:00Z', 'SUCCESS'),
    run('2026-10-02T12:00:01Z', 'FAILURE'),
    run('2026-09-20T12:00:00Z', 'SUCCESS'),
    run('2026-10-01T14:00:00Z', 'STARTED'),
  ]

  it('uses both time boundaries and distinct longer ranges', () => {
    expect(
      runsInRange(runs, '24h', observedAt).map((item) => item.run_id),
    ).toEqual([
      '2026-10-01T12:00:00Z',
      '2026-10-01T14:30:00Z',
      '2026-10-02T12:00:00Z',
      '2026-10-01T14:00:00Z',
    ])
    expect(runsInRange(runs, '7d', observedAt)).toHaveLength(5)
    expect(runsInRange(runs, '30d', observedAt)).toHaveLength(6)
    const buckets = runBuckets(runs, '24h', observedAt)
    expect(buckets).toHaveLength(24)
    expect(buckets[0]).toEqual({
      timestamp: '2026-10-01T12:00:00.000Z',
      succeeded: 0,
      failed: 1,
    })
    expect(buckets[2]).toEqual({
      timestamp: '2026-10-01T14:00:00.000Z',
      succeeded: 1,
      failed: 0,
    })
    expect(buckets[23]?.succeeded).toBe(1)
    expect(
      buckets.reduce(
        (total, bucket) => total + bucket.succeeded + bucket.failed,
        0,
      ),
    ).toBe(3)
  })

  it('does not render unknown freshness as healthy or unhealthy', () => {
    const markup = renderToStaticMarkup(
      createElement(HealthBar, { ok: 0, bad: 0, unknown: 11 }),
    )
    expect(markup).toContain('0 healthy, 0 unhealthy, 11 unknown')
  })

  it('separates successful and active observations from missing job history', () => {
    expect(observedRunState(run(observedAt, 'SUCCESS'))).toBe('succeeded')
    expect(observedRunState(run(observedAt, 'FAILURE'))).toBe('failed')
    expect(observedRunState(run(observedAt, 'STARTED'))).toBe('other')
    expect(observedRunState()).toBe('unknown')
  })

  it('uses explicit group metadata without inventing layers for other groups', () => {
    expect(assetLayer({ key: ['stg_devices'], group_name: 'silver' })).toBe(
      'silver',
    )
    expect(assetLayer({ key: ['gold', 'orders'], group_name: 'finance' })).toBe(
      'gold',
    )
    expect(
      assetLayer({ key: ['fleet_daily_summary'], group_name: 'transform' }),
    ).toBeUndefined()
  })

  it('charts actual numeric values without coercing strings or all-null columns', () => {
    expect(
      numericColumns({
        columns: ['metric', 'id', 'empty'].map((name) => ({
          name,
          type: null,
        })),
        rows: [
          { metric: 2.5, id: '9007199254740993', empty: null },
          { metric: null, id: '7', empty: null },
        ],
        has_more: false,
      }).map((column) => column.name),
    ).toEqual(['metric'])
  })

  it('shows the latest check result by timestamp and leaves missing evidence neutral', () => {
    const markup = renderToStaticMarkup(
      createElement(AuditsTab, {
        addAudit: null,
        data: {
          env: 'prod',
          history: { status: 'complete', unverifiable_checks: [] },
          definitions: [
            { name: 'recent', description: null },
            { name: 'never_run', description: null },
          ],
          executions: [
            {
              check_name: 'recent',
              run_id: 'older',
              timestamp: '2026-10-01T12:00:00Z',
              passed: true,
              status: 'SUCCEEDED',
              severity: null,
            },
            {
              check_name: 'recent',
              run_id: 'newer',
              timestamp: '2026-10-02T12:00:00Z',
              passed: false,
              status: 'SUCCEEDED',
              severity: 'ERROR',
            },
          ],
        },
      }),
    )
    expect(markup).toContain('title="2026-10-02T12:00:00Z">Failed')
    expect(markup).toContain('No execution')
    expect(markup).toContain('2 recorded check executions, newest first')
  })

  it('hides outcomes for checks whose execution identity is unverifiable', () => {
    const markup = renderToStaticMarkup(
      createElement(AuditsTab, {
        addAudit: null,
        data: {
          env: 'prod',
          history: {
            status: 'partial',
            unverifiable_checks: ['registry_quality'],
          },
          definitions: [{ name: 'registry_quality', description: null }],
          executions: [
            {
              check_name: 'registry_quality',
              run_id: 'verified-but-not-authoritative',
              timestamp: '2026-10-02T12:00:00Z',
              passed: true,
              status: 'SUCCEEDED',
              severity: null,
            },
          ],
        },
      }),
    )
    expect(markup).toContain('History unavailable')
    expect(markup).toContain('Some execution history could not be verified')
    expect(markup).not.toContain('No execution')
    expect(markup).not.toContain('Passed')
    expect(markup).not.toContain('verified-but-not-authoritative')
  })

  it('preserves large snapshot identifiers and summary counts without numeric coercion', () => {
    const markup = renderToStaticMarkup(
      createElement(SnapshotsTab, {
        data: {
          env: 'staging',
          nessie_ref: 'staging',
          items: [
            {
              snapshot_id: '9007199254740993',
              timestamp_ms: 1_759_401_000_000,
              operation: 'append',
              summary: {
                'added-records': '9007199254740995',
                'added-data-files': '7',
              },
            },
          ],
        },
      }),
    )
    expect(markup).toContain('9007199254740993')
    expect(markup).toContain('9007199254740995')
    expect(markup).not.toContain('9007199254740992')
    expect(markup).toContain('Nessie ref')
    expect(markup).toContain('staging')
  })
})
