import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}))

vi.mock('@/server/phlo-api', () => ({ apiGet, apiPost }))

describe('v1 query-workspace client', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
  })

  it('submits bounded SQL to the selected environment and preserves identity', async () => {
    const { submitQueryFromApi } = await import('@/observatory/api/trino')
    const session = {
      id: 'query-1',
      env: 'prod',
      nessie_ref: 'main',
      status: 'queued',
      sql_hash: 'hash',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      result: null,
      error: null,
    }
    apiPost.mockResolvedValue(session)

    await expect(
      submitQueryFromApi({
        query: 'SELECT order_id FROM gold.orders',
        rowLimit: 25,
        environment: 'prod',
        authorization: 'Bearer query-token',
      }),
    ).resolves.toEqual(session)
    expect(apiPost).toHaveBeenCalledWith(
      '/api/v1/queries?env=prod',
      { sql: 'SELECT order_id FROM gold.orders', row_limit: 25 },
      30000,
      'Bearer query-token',
    )
  })

  it('retrieves a query session instead of assuming a synchronous result', async () => {
    const { getQuerySessionFromApi } = await import('@/observatory/api/trino')
    apiGet.mockResolvedValue({ id: 'query-1', status: 'running' })

    await getQuerySessionFromApi({
      queryId: 'query/1',
      environment: 'staging',
      authorization: 'Bearer query-token',
    })
    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/queries/query%2F1?env=staging',
      undefined,
      30000,
      'Bearer query-token',
    )
  })

  it('loads saved queries from the selected v1 environment and forwards identity', async () => {
    const { getSavedQueriesFromApi } = await import('@/observatory/api/trino')
    const saved = {
      id: 'query-1',
      env: 'staging',
      nessie_ref: 'staging',
      name: 'Orders',
      sql: 'SELECT id FROM analytics.orders',
      version: 1,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      metadata: {},
    }
    apiGet.mockResolvedValue({ env: 'staging', items: [saved] })

    await expect(
      getSavedQueriesFromApi({
        environment: 'staging',
        authorization: 'Bearer analyst-token',
      }),
    ).resolves.toEqual([saved])
    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/queries/saved?env=staging',
      undefined,
      30000,
      'Bearer analyst-token',
    )
  })

  it('creates a saved query with server-enforced environment and idempotency', async () => {
    const { createSavedQueryFromApi } = await import('@/observatory/api/trino')
    const saved = {
      id: 'query-1',
      env: 'prod',
      nessie_ref: 'main',
      name: 'Orders',
      sql: 'SELECT id FROM analytics.orders',
      version: 1,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      metadata: {},
    }
    apiPost.mockResolvedValue(saved)

    await expect(
      createSavedQueryFromApi({
        environment: 'prod',
        idempotencyKey: 'saved-query-key-1',
        name: 'Orders',
        sql: 'SELECT id FROM analytics.orders',
        authorization: 'Bearer analyst-token',
      }),
    ).resolves.toEqual(saved)
    expect(apiPost).toHaveBeenCalledWith(
      '/api/v1/queries/saved?env=prod',
      {
        env: 'prod',
        name: 'Orders',
        sql: 'SELECT id FROM analytics.orders',
        metadata: {},
      },
      30000,
      'Bearer analyst-token',
      { 'Idempotency-Key': 'saved-query-key-1' },
    )
  })

  it('polls a v1 session and maps completed rows into the query result', async () => {
    const { executeQueryFromApi } = await import('@/observatory/api/trino')
    apiPost.mockResolvedValue({
      id: 'query-1',
      env: 'prod',
      status: 'queued',
      result: null,
      error: null,
    })
    apiGet
      .mockResolvedValueOnce({ id: 'query-1', env: 'prod', status: 'running' })
      .mockResolvedValueOnce({
        id: 'query-1',
        env: 'prod',
        status: 'completed',
        result: {
          columns: [
            { name: 'order_id', type: 'varchar' },
            { name: 'amount', type: 'decimal' },
          ],
          rows: [{ order_id: 'order-1', amount: null }],
          has_more: false,
        },
        error: null,
      })

    await expect(
      executeQueryFromApi({
        query: 'SELECT order_id, amount FROM gold.orders',
        environment: 'prod',
      }),
    ).resolves.toEqual({
      columns: ['order_id', 'amount'],
      columnTypes: ['varchar', 'decimal'],
      rows: [{ order_id: 'order-1', amount: null }],
      hasMore: false,
      effectiveQuery: 'SELECT order_id, amount FROM gold.orders',
    })
    expect(apiGet).toHaveBeenCalledTimes(2)
  })

  it('returns a terminal v1 query failure without inventing an empty result', async () => {
    const { executeQueryFromApi } = await import('@/observatory/api/trino')
    apiPost.mockResolvedValue({
      id: 'query-1',
      env: 'staging',
      status: 'failed',
      result: null,
      error: 'Query engine is unavailable or rejected the query.',
    })

    await expect(
      executeQueryFromApi({ query: 'SELECT 1', environment: 'staging' }),
    ).resolves.toEqual({
      ok: false,
      kind: 'trino',
      error: 'Query engine is unavailable or rejected the query.',
    })
    expect(apiGet).not.toHaveBeenCalled()
  })

  it('fails closed when no environment is selected', async () => {
    const { submitQueryFromApi } = await import('@/observatory/api/trino')
    await expect(submitQueryFromApi({ query: 'SELECT 1' })).rejects.toThrow(
      'Select prod or staging',
    )
    expect(apiPost).not.toHaveBeenCalled()
  })
})
