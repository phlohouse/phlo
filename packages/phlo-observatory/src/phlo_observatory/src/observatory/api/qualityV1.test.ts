import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
vi.mock('@/server/phlo-api', () => ({ apiGet }))

const assets = {
  env: 'prod',
  items: [{ id: 'warehouse/orders' }],
  next_cursor: null,
}
const checks = {
  env: 'prod',
  asset_id: 'warehouse/orders',
  definitions: [{ name: 'not_null_id', description: null }],
  executions: [
    {
      status: 'SUCCEEDED',
      run_id: 'run-1',
      timestamp: '2026-09-28T12:00:00Z',
      check_name: 'not_null_id',
      passed: true,
      severity: 'ERROR',
      metadata: [],
    },
  ],
}

describe('v1 quality client', () => {
  beforeEach(() => apiGet.mockReset())

  it('uses the selected environment for the asset inventory and each checks request', async () => {
    apiGet.mockResolvedValueOnce(assets).mockResolvedValueOnce(checks)
    const { getV1QualitySnapshotData } = await import('./qualityV1')

    await expect(getV1QualitySnapshotData('prod')).resolves.toMatchObject({
      kind: 'available',
      data: {
        assets: [
          { kind: 'available', checks: { executions: [{ passed: true }] } },
        ],
      },
    })
    expect(apiGet).toHaveBeenNthCalledWith(1, '/api/v1/assets', {
      env: 'prod',
      limit: 100,
    })
    expect(apiGet).toHaveBeenNthCalledWith(
      2,
      '/api/v1/assets/warehouse/orders/checks',
      { env: 'prod' },
    )
  })

  it('keeps a missing or null check result unavailable rather than passing', async () => {
    apiGet.mockResolvedValueOnce(assets).mockResolvedValueOnce({
      ...checks,
      executions: [{ ...checks.executions[0], passed: null }],
    })
    const { getV1QualitySnapshotData } = await import('./qualityV1')

    await expect(getV1QualitySnapshotData('prod')).resolves.toMatchObject({
      kind: 'available',
      data: {
        assets: [
          { kind: 'available', checks: { executions: [{ passed: null }] } },
        ],
      },
    })
  })

  it('reports malformed or mismatched evidence as unavailable', async () => {
    apiGet
      .mockResolvedValueOnce(assets)
      .mockResolvedValueOnce({ ...checks, env: 'staging' })
    const { getV1QualitySnapshotData } = await import('./qualityV1')

    await expect(getV1QualitySnapshotData('prod')).resolves.toMatchObject({
      kind: 'available',
      data: { assets: [{ kind: 'unavailable', assetId: 'warehouse/orders' }] },
    })
  })
})
