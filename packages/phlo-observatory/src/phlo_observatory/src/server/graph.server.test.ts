/** Tests asset-graph API wrappers against mocked phlo-api responses. */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()

vi.mock('@/server/phlo-api', () => ({
  apiGet,
}))

describe('graph.server api wrappers', () => {
  beforeEach(() => {
    apiGet.mockReset()
  })

  it('fetches the asset graph from phlo-api', async () => {
    const { fetchAssetGraphFromApi } = await import('@/observatory/api/graph')
    const payload = {
      nodes: [
        {
          id: 'gold.fct_orders',
          key: ['gold', 'fct_orders'],
          key_path: 'gold.fct_orders',
          label: 'fct_orders',
          layer: 'gold',
          upstream_count: 1,
          downstream_count: 0,
        },
        {
          id: 'silver.stg_orders',
          key: ['silver', 'stg_orders'],
          key_path: 'silver.stg_orders',
          label: 'stg_orders',
          layer: 'silver',
          upstream_count: 0,
          downstream_count: 1,
        },
      ],
      edges: [{ source: 'silver.stg_orders', target: 'gold.fct_orders' }],
    }
    apiGet.mockResolvedValue(payload)

    const result = await fetchAssetGraphFromApi()

    expect(result).toEqual({
      nodes: [
        expect.objectContaining({
          id: 'gold.fct_orders',
          key_path: 'gold.fct_orders',
          label: 'fct_orders',
          downstream_count: 0,
          upstream_count: 1,
        }),
        expect.objectContaining({
          id: 'silver.stg_orders',
          key_path: 'silver.stg_orders',
          label: 'stg_orders',
          downstream_count: 1,
          upstream_count: 0,
        }),
      ],
      edges: [{ source: 'silver.stg_orders', target: 'gold.fct_orders' }],
    })
    expect(apiGet).toHaveBeenCalledWith('/api/observatory/asset-graph')
  })

  it('fetches impact data from phlo-api', async () => {
    const { fetchAssetImpactFromApi } = await import('@/observatory/api/graph')
    const payload = [
      {
        key_path: 'gold.fct_orders',
        label: 'fct_orders',
        layer: 'gold',
        depth: 1,
      },
    ]
    apiGet.mockResolvedValue(payload)

    const result = await fetchAssetImpactFromApi({
      assetKey: 'silver.stg_orders',
      maxDepth: 2,
    })

    expect(result).toEqual([
      {
        key_path: 'gold.fct_orders',
        label: 'fct_orders',
        layer: 'gold',
        depth: 1,
      },
    ])
    expect(apiGet).toHaveBeenCalledWith('/api/observatory/asset-graph/impact', {
      asset_key: 'silver.stg_orders',
      max_depth: 2,
    })
  })
})
