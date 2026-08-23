/**
 * Tests Trino v2 server wrappers (table preview, query execution) against
 * mocked phlo-api responses.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}))

vi.mock('@/server/phlo-api', () => ({
  apiGet,
  apiPost,
}))

describe('trino.server v2 wrappers', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
  })

  it('previews table data through the v2 table preview endpoint', async () => {
    const { previewDataFromApi } = await import('@/observatory/api/trino')
    apiGet.mockResolvedValue({
      columns: ['order_id'],
      column_types: ['varchar'],
      rows: [{ order_id: 'abc123' }],
      row_count: 1,
      has_more: false,
    })

    const result = await previewDataFromApi({
      table: 'gold.fct_orders',
      limit: 25,
      offset: 50,
    })

    expect(result).toEqual({
      columns: ['order_id'],
      columnTypes: ['varchar'],
      rows: [{ order_id: 'abc123' }],
      totalRows: 1,
      hasMore: false,
    })
    expect(apiGet).toHaveBeenCalledWith(
      '/api/observatory/table-preview/gold.fct_orders',
      { limit: 25, offset: 50 },
    )
  })

  it('runs read queries through the v2 query endpoint', async () => {
    const { executeQueryFromApi } = await import('@/observatory/api/trino')
    apiPost.mockResolvedValue({
      columns: ['order_id'],
      column_types: ['varchar'],
      rows: [{ order_id: 'abc123' }],
      row_count: 1,
      has_more: false,
      effective_sql: 'select * from gold.fct_orders limit 10',
    })

    const result = await executeQueryFromApi({
      query: 'select * from gold.fct_orders',
      branch: 'main',
      defaultLimit: 10,
    })

    expect(result).toEqual({
      columns: ['order_id'],
      columnTypes: ['varchar'],
      rows: [{ order_id: 'abc123' }],
      totalRows: 1,
      hasMore: false,
      effectiveQuery: 'select * from gold.fct_orders limit 10',
    })
    expect(apiPost).toHaveBeenCalledWith('/api/observatory/query', {
      sql: 'select * from gold.fct_orders',
      branch: 'main',
      limit: 10,
      offset: 0,
    })
  })
})
