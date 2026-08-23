/**
 * Tests the Dagster-backed quality snapshot: counts, failing checks, and
 * recent executions computed from GraphQL responses.
 */
import { describe, expect, it, vi } from 'vitest'

describe('fetchQualitySnapshot', () => {
  it('computes counts, failing checks, and recent executions', async () => {
    const { fetchQualitySnapshot } = await import('@/server/quality.dagster')

    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? '{}')) as {
        query?: string
        variables?: Record<string, unknown>
      }

      if (body.query?.includes('assetNodes')) {
        return okJson({
          data: {
            assetNodes: [
              {
                assetKey: { path: ['dlt_orders'] },
                assetChecksOrError: {
                  __typename: 'AssetChecks',
                  checks: [
                    { name: 'pandera_contract', description: 'Contract' },
                  ],
                },
              },
              {
                assetKey: { path: ['mrt_orders'] },
                assetChecksOrError: {
                  __typename: 'AssetChecks',
                  checks: [
                    {
                      name: 'dbt__not_null__mrt_orders__id',
                      description: 'NN',
                    },
                    {
                      name: 'dbt__unique__mrt_orders__id',
                      description: 'Unique',
                    },
                  ],
                },
              },
            ],
          },
        })
      }

      if (body.query?.includes('assetCheckExecutions')) {
        const assetKey = (
          body.variables?.assetKey as { path: Array<string> } | undefined
        )?.path
        if (assetKey?.join('/') === 'dlt_orders') {
          return okJson({
            data: {
              assetCheckExecutions: [
                {
                  status: 'FAILED',
                  runId: 'run-1',
                  timestamp: 1_700_000_000,
                  checkName: 'pandera_contract',
                  evaluation: { severity: 'ERROR', metadataEntries: [] },
                },
              ],
            },
          })
        }
        if (assetKey?.join('/') === 'mrt_orders') {
          return okJson({
            data: {
              assetCheckExecutions: [
                {
                  status: 'FAILED',
                  runId: 'run-2',
                  timestamp: 1_700_000_100,
                  checkName: 'dbt__not_null__mrt_orders__id',
                  evaluation: { severity: 'WARN', metadataEntries: [] },
                },
                {
                  status: 'SUCCEEDED',
                  runId: 'run-3',
                  timestamp: 1_700_000_200,
                  checkName: 'dbt__unique__mrt_orders__id',
                  evaluation: { severity: 'ERROR', metadataEntries: [] },
                },
              ],
            },
          })
        }
      }

      return okJson({ errors: [{ message: 'unexpected query' }] }, 400)
    })

    vi.stubGlobal('fetch', fetchMock)
    process.env.DAGSTER_GRAPHQL_URL = 'http://dagster/graphql'

    const result = await fetchQualitySnapshot({
      recentLimit: 10,
      timeoutMs: 1000,
    })
    expect('error' in result).toBe(false)

    if ('error' in result) return

    expect(result.totalChecks).toBe(3)
    expect(result.passingChecks).toBe(1)
    expect(result.warningChecks).toBe(1)
    expect(result.failingChecks).toBe(1)
    expect(result.latestChecks).toHaveLength(3)
    expect(result.failingChecksList).toHaveLength(1)
    expect(result.failingChecksList[0]?.name).toBe('pandera_contract')
    expect(result.recentExecutions.length).toBeGreaterThan(0)
  })
})

function okJson(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
