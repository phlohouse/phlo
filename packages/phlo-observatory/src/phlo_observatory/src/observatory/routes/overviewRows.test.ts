/**
 * Tests overview-row builders: attention items, event story assembly, and
 * blocking quality-issue classification.
 */
import { describe, expect, it } from 'vitest'

import {
  buildAttentionItems,
  buildEventStory,
  isBlockingQualityIssue,
} from './OverviewRoute'
import type {
  ObservatoryLogEvent,
  ObservatoryOperation,
  ObservatoryQualityCheck,
  ObservatoryService,
} from '@/observatory/api/types'

function service(
  overrides: Partial<ObservatoryService> = {},
): ObservatoryService {
  return {
    id: 'dagster',
    name: 'Dagster',
    package: 'phlo-dagster',
    status: 'stopped',
    health: { state: 'error', message: 'runtime stopped' },
    links: [],
    in_stack: true,
    definition_state: 'configured',
    metadata: {},
    ...overrides,
  } as ObservatoryService
}

function operation(
  overrides: Partial<ObservatoryOperation> = {},
): ObservatoryOperation {
  return {
    id: 'revenue-refresh-20260702',
    name: 'Refresh Revenue Draft',
    kind: 'pipeline_run',
    status: 'failed',
    health: { state: 'error', message: 'Reconciliation check failed.' },
    target: {
      kind: 'dataset',
      id: 'gold.revenue',
      label: 'Revenue Draft',
    },
    started_at: '2026-07-02T08:29:00Z',
    completed_at: '2026-07-02T08:29:11Z',
    duration_seconds: 11,
    metadata: { failure_reason: 'Reconciliation check failed.' },
    ...overrides,
  } as ObservatoryOperation
}

function quality(
  overrides: Partial<ObservatoryQualityCheck> = {},
): ObservatoryQualityCheck {
  return {
    id: 'gold.revenue:reconciliation',
    name: 'Revenue reconciles to billing',
    asset_id: 'gold.revenue',
    status: 'failing',
    severity: 'critical',
    blocking: true,
    ...overrides,
  } as ObservatoryQualityCheck
}

function log(
  overrides: Partial<ObservatoryLogEvent> = {},
): ObservatoryLogEvent {
  return {
    id: 'log-1',
    message: 'Revenue reconciliation is outside tolerance.',
    level: 'error',
    source: 'observatory-fixture',
    timestamp: '2026-07-02T08:29:11Z',
    resource: {
      kind: 'dataset',
      id: 'gold.revenue',
      label: 'Revenue Draft',
    },
    metadata: {},
    ...overrides,
  } as ObservatoryLogEvent
}

describe('Overview control-room rows', () => {
  it('links attention rows to exact evidence routes', () => {
    const rows = buildAttentionItems({
      services: [service()],
      operations: [operation()],
      quality: [
        quality({
          id: 'silver.orders:late_arrivals',
          name: 'Late arrivals monitored',
          asset_id: 'silver.orders',
          status: 'warning',
          severity: 'medium',
        }),
        quality(),
      ],
      logs: [],
      enabled: { logs: true, operations: true, quality: true },
    })

    expect(rows.map((row) => [row.kind, row.href])).toEqual([
      ['service', '/services?serviceId=dagster'],
      ['quality', '/quality?checkId=gold.revenue%3Areconciliation'],
      ['quality', '/quality?checkId=silver.orders%3Alate_arrivals'],
      ['operation', '/operations?operationId=revenue-refresh-20260702'],
    ])
    expect(rows.find((row) => row.kind === 'quality')?.reason).toContain(
      'impact',
    )
  })

  it('does not duplicate logs already represented by failing checks or operations', () => {
    const rows = buildAttentionItems({
      services: [],
      operations: [operation()],
      quality: [quality()],
      logs: [
        log(),
        log({
          id: 'operation-log',
          resource: { kind: 'dataset', id: 'gold.revenue', label: 'Revenue' },
        }),
      ],
      enabled: { logs: true, operations: true, quality: true },
    })

    expect(rows.map((row) => row.kind)).toEqual(['quality', 'operation'])
  })

  it('counts only non-passing blocking checks as active blockers', () => {
    expect(isBlockingQualityIssue(quality({ status: 'passing' }))).toBe(false)
    expect(isBlockingQualityIssue(quality({ status: 'warning' }))).toBe(true)
    expect(isBlockingQualityIssue(quality({ status: 'failing' }))).toBe(true)
    expect(
      isBlockingQualityIssue(quality({ blocking: false, status: 'failing' })),
    ).toBe(false)
  })

  it('keeps the event story focused on relevant operational evidence', () => {
    const story = buildEventStory(
      [operation()],
      [
        log({ id: 'actionable-log' }),
        log({
          id: 'plugin-noise',
          message: 'plugin_load_failed',
          source: 'phlo.plugins.discovery._plugin_loading',
          resource: {
            kind: 'service',
            id: 'phlo-api',
            label: 'Phlo API',
          },
        }),
      ],
    )

    expect(
      story.events.map((event) => [event.kind, event.href]),
    ).toContainEqual([
      'operation',
      '/operations?operationId=revenue-refresh-20260702',
    ])
    expect(
      story.events.map((event) => [event.kind, event.href]),
    ).toContainEqual(['log', '/logs?logId=actionable-log'])
    expect(story.events.map((event) => event.id)).not.toContain(
      'log:plugin-noise',
    )
  })
})
