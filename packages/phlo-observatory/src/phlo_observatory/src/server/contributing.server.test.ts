/**
 * Tests contributing-data server transforms and query payloads stay stable
 * end to end against mocked phlo-api responses.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchContributingRowsPageFromApi,
  fetchContributingRowsQueryFromApi,
  transformContributingRowsPageResult,
  transformContributingRowsQueryResult,
} from '@/observatory/api/contributing'

const { apiPost } = vi.hoisted(() => ({
  apiPost: vi.fn(),
}))

vi.mock('@/server/phlo-api', () => ({
  apiPost,
}))

describe('contributing.server transforms', () => {
  beforeEach(() => {
    apiPost.mockReset()
  })

  it('keeps contributing query payload stable', () => {
    expect(
      transformContributingRowsQueryResult({
        query: 'SELECT * FROM foo LIMIT 10',
        upstream: { schema: 'gold', table: 'fct_orders' },
      }),
    ).toEqual({
      query: 'SELECT * FROM foo LIMIT 10',
      upstream: { schema: 'gold', table: 'fct_orders' },
    })
  })

  it('camelizes contributing page payload from phlo-api', () => {
    expect(
      transformContributingRowsPageResult({
        mode: 'aggregate',
        page: 2,
        page_size: 50,
        has_more: true,
        query: 'SELECT * FROM foo OFFSET 100 LIMIT 51',
        upstream: { schema: 'silver', table: 'stg_orders' },
        columns: ['order_id'],
        column_types: ['varchar'],
        rows: [{ order_id: 'abc123' }],
      }),
    ).toEqual({
      mode: 'aggregate',
      page: 2,
      pageSize: 50,
      hasMore: true,
      query: 'SELECT * FROM foo OFFSET 100 LIMIT 51',
      upstream: { schema: 'silver', table: 'stg_orders' },
      columns: ['order_id'],
      columnTypes: ['varchar'],
      rows: [{ order_id: 'abc123' }],
    })
  })

  it('fetches contributing query payload through v2', async () => {
    const payload = {
      query: 'SELECT * FROM foo LIMIT 10',
      upstream: { schema: 'gold', table: 'fct_orders' },
    }
    apiPost.mockResolvedValue(payload)

    const result = await fetchContributingRowsQueryFromApi({
      downstreamAssetKey: 'gold.fct_orders',
      upstreamAssetKey: 'silver.stg_orders',
      rowData: { order_id: 'abc123' },
      limit: 25,
      trinoUrl: 'http://trino:8080',
    })

    expect(result).toEqual(payload)
    expect(apiPost).toHaveBeenCalledWith(
      '/api/observatory/contributing-rows/query',
      {
        downstream_asset_key: 'gold.fct_orders',
        upstream_asset_key: 'silver.stg_orders',
        row_data: { order_id: 'abc123' },
        limit: 25,
        trino_url: 'http://trino:8080',
        timeout_ms: undefined,
        catalog: undefined,
      },
    )
  })

  it('fetches contributing page payload through v2', async () => {
    const payload = {
      mode: 'aggregate' as const,
      page: 2,
      page_size: 50,
      has_more: true,
      query: 'SELECT * FROM foo OFFSET 50 LIMIT 51',
      upstream: { schema: 'silver', table: 'stg_orders' },
      columns: ['order_id'],
      column_types: ['varchar'],
      rows: [{ order_id: 'abc123' }],
    }
    apiPost.mockResolvedValue(payload)

    const result = await fetchContributingRowsPageFromApi({
      downstreamAssetKey: 'gold.fct_orders',
      upstreamAssetKey: 'silver.stg_orders',
      rowData: { order_id: 'abc123' },
      page: 2,
      pageSize: 50,
      catalog: 'iceberg',
    })

    expect(result).toEqual(payload)
    expect(apiPost).toHaveBeenCalledWith(
      '/api/observatory/contributing-rows/page',
      {
        downstream_asset_key: 'gold.fct_orders',
        upstream_asset_key: 'silver.stg_orders',
        row_data: { order_id: 'abc123' },
        page: 2,
        page_size: 50,
        trino_url: undefined,
        timeout_ms: undefined,
        catalog: 'iceberg',
      },
    )
  })
})
