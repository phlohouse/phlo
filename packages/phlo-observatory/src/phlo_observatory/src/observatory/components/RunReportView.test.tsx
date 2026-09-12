// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RunReportView } from './RunReportView'
import type { ReactNode } from 'react'
import type { ObservatoryRunReport } from '@/observatory/api/types'

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    className,
    to,
  }: {
    children?: ReactNode
    className?: string
    to: string
  }) => (
    <a className={className} href={to}>
      {children}
    </a>
  ),
}))

const request = { projectId: 'finance', runId: 'daily-orders', attempt: '2' }

describe('RunReportView', () => {
  afterEach(cleanup)

  it('renders loading, empty, denied, not-found, and generic error states', () => {
    const { rerender } = render(
      <RunReportView request={request} result={null} />,
    )
    expect(screen.getByText('Loading run report')).toBeTruthy()

    rerender(
      <RunReportView
        request={request}
        result={{
          data: null,
          error: 'No evidence',
          errorCode: 'not_found',
        }}
      />,
    )
    expect(screen.getByText('Run report not found')).toBeTruthy()

    rerender(
      <RunReportView
        request={request}
        result={{
          data: null,
          error: 'Access denied',
          errorCode: 'access_denied',
        }}
      />,
    )
    expect(screen.getByRole('heading', { name: 'Access denied' })).toBeTruthy()

    rerender(
      <RunReportView
        request={request}
        result={{
          data: null,
          error: 'Network failed',
          errorCode: 'request_failed',
        }}
      />,
    )
    expect(screen.getByText('Run report unavailable')).toBeTruthy()

    rerender(
      <RunReportView
        request={request}
        result={{
          data: emptyReport(),
          error: null,
        }}
      />,
    )
    expect(screen.getByText('No attempt-scoped evidence recorded')).toBeTruthy()
    expect(screen.getByText('no_attempt_scoped_evidence')).toBeTruthy()
  })

  it('renders every evidence area and preserves explicit unknowns and gaps', () => {
    render(
      <RunReportView
        request={request}
        result={{ data: populatedReport(), error: null }}
      />,
    )

    for (const label of [
      'Lifecycle',
      'Stages and transformations',
      'Inputs, staging, and outputs',
      'Lineage',
      'Quality',
      'Catalog / Nessie evidence',
      'Artifacts',
      'Outcome and gaps',
      'Explicit gaps',
    ]) {
      expect(screen.getByText(label)).toBeTruthy()
    }
    expect(screen.getByText('terminal')).toBeTruthy()
    expect(screen.getByText('historical_fields · unavailable')).toBeTruthy()
    expect(screen.getByText('event.started · dagster')).toBeTruthy()
    expect(screen.getByText('orders_raw → orders_gold')).toBeTruthy()
    expect(
      screen.getByText('failure-artifact · report · available'),
    ).toBeTruthy()
  })

  it.each([
    [
      'transformation-only evidence',
      () => ({
        ...emptyReport(),
        transformations: [populatedReport().stages[0]],
      }),
      'Transformations',
    ],
    [
      'Iceberg-only evidence',
      () => ({
        ...emptyReport(),
        iceberg_snapshots: [resource('output', 'orders_gold')],
      }),
      'Iceberg snapshots',
    ],
  ])('renders %s instead of the empty state', (_, report, evidenceLabel) => {
    render(
      <RunReportView
        request={request}
        result={{ data: report(), error: null }}
      />,
    )

    expect(screen.queryByText('No attempt-scoped evidence recorded')).toBeNull()
    expect(screen.getByText(evidenceLabel)).toBeTruthy()
  })
})

function emptyReport(): ObservatoryRunReport {
  return {
    ...populatedReport(),
    lifecycle: { run: null, events: [] },
    stages: [],
    inputs: [],
    staging: [],
    outputs: [],
    lineage: [],
    transformations: [],
    quality: [],
    iceberg_snapshots: [],
    catalog_changes: [],
    artifacts: [],
    terminal_outcome: null,
    gaps: [
      {
        field: 'stages',
        status: 'unavailable',
        reason: 'no_attempt_scoped_evidence',
      },
    ],
  }
}

function populatedReport(): ObservatoryRunReport {
  return {
    schema_version: 1,
    project_id: 'finance',
    run_id: 'daily-orders',
    attempt: 2,
    lifecycle: {
      run: {
        project_id: 'finance',
        run_id: 'daily-orders',
        pipeline_name: 'orders',
        provider_run_id: 'dagster-42',
        attempt: 2,
        status: 'failed',
        started_at: '2026-07-15T10:00:00Z',
        finished_at: '2026-07-15T10:02:00Z',
        failure_summary: 'quality check failed',
        evidence_completeness: 'partial',
      },
      events: [
        {
          event_id: 'event-1',
          producer: 'dagster',
          event_type: 'event.started',
          observed_at: '2026-07-15T10:00:00Z',
          sequence: 1,
          payload_checksum: 'checksum',
        },
      ],
    },
    stages: [
      {
        stage_id: 'load',
        stage_type: 'transform',
        provider: 'dbt',
        tool: 'dbt',
        asset: 'orders_gold',
        status: 'failed',
        started_at: '2026-07-15T10:00:00Z',
        finished_at: '2026-07-15T10:02:00Z',
        error_fingerprint: 'error-1',
      },
    ],
    inputs: [resource('input', 'orders_raw')],
    staging: [resource('staged', 'orders_stage')],
    outputs: [resource('output', 'orders_gold')],
    lineage: [
      {
        lineage_edge_id: 'edge-1',
        source: 'orders_raw',
        target: 'orders_gold',
        origin: 'observed',
        derivation: 'transform',
      },
    ],
    transformations: [],
    quality: [
      {
        quality_result_id: 'quality-1',
        check_id: 'orders_not_null',
        asset: 'orders_gold',
        stage_id: 'load',
        severity: 'error',
        blocking: true,
        passed: false,
        evaluated_count: 100,
        failed_count: 2,
        failure_artifact_id: 'failure-artifact',
      },
    ],
    iceberg_snapshots: [resource('output', 'orders_gold')],
    catalog_changes: [
      {
        catalog_change_id: 'nessie-1',
        catalog_ref: 'main',
        content_key: 'orders_gold',
        operation: 'commit',
        source_hash: 'before',
        target_hash: 'after',
        commit_hash: 'commit-1',
        merge_outcome: 'accepted',
        snapshot_before: 'snap-1',
        snapshot_after: 'snap-2',
        metadata: { ref: 'main' },
      },
    ],
    artifacts: [
      {
        artifact_id: 'failure-artifact',
        artifact_kind: 'report',
        uri: 's3://reports/failure.json',
        content_type: 'application/json',
        checksum: 'checksum',
        expires_at: null,
        legal_hold: false,
        status: 'available',
      },
    ],
    terminal_outcome: {
      status: 'failed',
      source: 'event-1',
      evidence_id: 'terminal',
      observed_at: '2026-07-15T10:02:00Z',
    },
    gaps: [
      {
        field: 'historical_fields',
        status: 'unavailable',
        reason: 'attempt_reconciliation_not_proven',
      },
    ],
  }
}

function resource(role: string, resourceId: string) {
  return {
    resource_id: resourceId,
    resource_kind: 'table',
    role,
    normalized_identity: resourceId,
    uri: `s3://warehouse/${resourceId}`,
    table_name: resourceId,
    catalog: 'lakehouse',
    ref_name: 'main',
    schema_hash: 'schema-1',
    record_count: 100,
    byte_count: 1024,
    staged_objects: [],
    snapshot_before: 'snap-1',
    snapshot_after: 'snap-2',
  }
}
