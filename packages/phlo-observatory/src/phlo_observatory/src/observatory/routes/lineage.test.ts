import { describe, expect, it } from 'vitest'

import type {
  ObservatoryAsset,
  ObservatoryLogEvent,
  ObservatoryTable,
} from '@/observatory/api/types'
import {
  latestRunLogsForAsset,
  lineageChecksForAsset,
  tablesForAsset,
} from '@/routes/lineage'

const asset: ObservatoryAsset = {
  id: 'orders',
  name: 'orders',
  kinds: [],
  dependencies: [],
  resources: [],
  checks: [],
  metadata: { last_run_id: 'run-2' },
}

const logs: Array<ObservatoryLogEvent> = [
  {
    id: 'run-1:event-1',
    level: 'info',
    message: 'unrelated run',
    metadata: { run_id: 'run-1' },
  },
  {
    id: 'run-2:event-1',
    level: 'info',
    message: 'latest asset run',
    metadata: { run_id: 'run-2' },
  },
]

describe('lineage run evidence', () => {
  it('shows only run logs matching the asset latest-run reference', () => {
    expect(latestRunLogsForAsset(asset, logs)).toEqual([logs[1]])
  })

  it('does not attach run logs when the asset has no latest-run reference', () => {
    expect(latestRunLogsForAsset({ ...asset, metadata: {} }, logs)).toEqual([])
  })
})

describe('lineage v1 quality evidence', () => {
  it('uses the latest v1 execution and keeps missing results unknown', () => {
    expect(
      lineageChecksForAsset({
        kind: 'available',
        assetId: 'warehouse/orders',
        checks: {
          env: 'prod',
          asset_id: 'warehouse/orders',
          definitions: [
            { name: 'not_null_id', description: 'IDs must be present.' },
            { name: 'unique_id', description: null },
          ],
          executions: [
            {
              status: 'FAILED',
              run_id: 'old-run',
              timestamp: '2026-09-27T12:00:00Z',
              check_name: 'not_null_id',
              passed: false,
              severity: 'ERROR',
              metadata: [],
            },
            {
              status: 'SUCCEEDED',
              run_id: 'latest-run',
              timestamp: '2026-09-28T12:00:00Z',
              check_name: 'not_null_id',
              passed: true,
              severity: 'ERROR',
              metadata: [],
            },
          ],
        },
      }),
    ).toEqual([
      expect.objectContaining({
        asset_id: 'warehouse/orders',
        id: 'warehouse/orders:not_null_id',
        status: 'passing',
        metadata: {
          run_id: 'latest-run',
          status: 'SUCCEEDED',
          timestamp: '2026-09-28T12:00:00Z',
        },
      }),
      expect.objectContaining({
        id: 'warehouse/orders:unique_id',
        status: 'unknown',
        metadata: {},
      }),
    ])
  })
})

describe('lineage ref-aware table evidence', () => {
  it('links a catalog table only when its schema and name match the declared relation', () => {
    const tables: Array<ObservatoryTable> = [
      {
        id: 'iceberg.analytics.orders',
        name: 'orders',
        schema_name: 'analytics',
        metadata: { catalog_name: 'iceberg' },
      },
      {
        id: 'iceberg.analytics.customers',
        name: 'customers',
        schema_name: 'analytics',
        metadata: { catalog_name: 'iceberg' },
      },
      {
        id: 'iceberg.staging.orders',
        name: 'orders',
        schema_name: 'staging',
        metadata: { catalog_name: 'iceberg' },
      },
    ]

    expect(
      tablesForAsset(
        { ...asset, metadata: { relation: 'analytics.orders' } },
        tables,
      ),
    ).toEqual([tables[0]])
  })
})
