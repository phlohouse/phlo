// Verify table projections use v1 asset evidence and retain unavailable states.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
vi.mock('@/server/phlo-api', () => ({ apiGet }))

const asset = {
  id: 'warehouse/orders',
  key: ['warehouse', 'orders'],
  description: null,
  compute_kind: 'dbt',
  group_name: null,
  is_source: false,
  dependencies: [],
  last_materialization_at: null,
  last_run_id: null,
  relation: 'warehouse.orders',
  history_scoped: true,
}

describe('v1 Tables client', () => {
  it('uses an explicit environment and bounded asset page', async () => {
    apiGet.mockResolvedValue({
      env: 'staging',
      items: [asset],
      next_cursor: null,
    })
    const { getV1AssetsPageData } = await import('./tablesV1')

    await expect(
      getV1AssetsPageData({ environment: 'staging', cursor: null }),
    ).resolves.toMatchObject({ kind: 'available' })
    expect(apiGet).toHaveBeenCalledWith('/api/v1/assets', {
      env: 'staging',
      limit: 100,
    })
  })

  it('makes malformed or cross-environment list responses unavailable', async () => {
    apiGet.mockResolvedValue({ env: 'prod', items: [asset], next_cursor: null })
    const { getV1AssetsPageData } = await import('./tablesV1')

    await expect(
      getV1AssetsPageData({ environment: 'staging', cursor: null }),
    ).resolves.toMatchObject({ kind: 'unavailable' })
  })

  it('rejects asset detail returned for a different requested asset', async () => {
    apiGet.mockResolvedValue({
      ...asset,
      id: 'warehouse/customers',
      columns: [],
      schema_observed_at: null,
      column_lineage: null,
    })
    const { getV1AssetDetailData } = await import('./tablesV1')

    await expect(
      getV1AssetDetailData({
        environment: 'staging',
        assetId: asset.id,
      }),
    ).resolves.toMatchObject({ kind: 'unavailable' })
  })

  it('keeps the Tables screen on the v1 read-only contracts', () => {
    const source = readFileSync(
      resolve(import.meta.dirname, '../../routes/tables.tsx'),
      'utf8',
    )

    expect(source).toContain('getV1AssetsPage')
    expect(source).toContain('getV1AssetDetail')
    expect(source).toContain('getV1AssetPreview')
    expect(source).toContain("searchParams.set('tableId', assetId)")
    expect(source).not.toContain('/api/observatory/tables')
    expect(source).not.toContain('table-preview')
    expect(source).not.toContain('runObservatoryQuery')
  })
})
