// Verify bounded run-log reads retain the requested environment and run identity.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
vi.mock('@/server/phlo-api', () => ({ apiGet }))

describe('v1 run log client', () => {
  beforeEach(() => apiGet.mockReset())

  it('loads bounded logs for the newest runs in the requested environment', async () => {
    apiGet
      .mockResolvedValueOnce({
        env: 'staging',
        items: [{ run_id: 'run-1' }, { run_id: 'run-2' }],
      })
      .mockResolvedValueOnce({
        env: 'staging',
        run_id: 'run-1',
        items: [
          {
            event_type: 'STEP_FAILURE',
            message: 'step failed',
            timestamp: '2026-09-28T12:00:00Z',
            step_key: 'load_orders',
          },
        ],
      })
      .mockResolvedValueOnce({
        env: 'staging',
        run_id: 'run-2',
        items: [],
      })
    const { getV1RunLogRecordsData } = await import('./logsV1')

    await expect(getV1RunLogRecordsData('staging')).resolves.toEqual({
      data: [
        {
          id: 'run-1:2026-09-28T12:00:00Z:0',
          timestamp: '2026-09-28T12:00:00Z',
          level: 'error',
          message: 'step failed',
          source: 'run',
          resource: { kind: 'run', id: 'run-1', label: 'run-1' },
          metadata: {
            run_id: 'run-1',
            event_type: 'STEP_FAILURE',
            step_key: 'load_orders',
          },
        },
      ],
      error: null,
    })
    expect(apiGet).toHaveBeenNthCalledWith(
      1,
      '/api/v1/runs?limit=100&env=staging',
      undefined,
      8000,
      undefined,
    )
    expect(apiGet).toHaveBeenNthCalledWith(
      2,
      '/api/v1/runs/run-1/logs?limit=50&env=staging',
      undefined,
      8000,
      undefined,
    )
    expect(apiGet).toHaveBeenNthCalledWith(
      3,
      '/api/v1/runs/run-2/logs?limit=50&env=staging',
      undefined,
      8000,
      undefined,
    )
  })

  it('returns unavailable when the API responds with logs for another run', async () => {
    apiGet
      .mockResolvedValueOnce({ env: 'prod', items: [{ run_id: 'run-1' }] })
      .mockResolvedValueOnce({
        env: 'prod',
        run_id: 'other-run',
        items: [],
      })
    const { getV1RunLogRecordsData } = await import('./logsV1')

    await expect(getV1RunLogRecordsData('prod')).resolves.toEqual({
      data: null,
      error: 'phlo-api returned logs for another run or environment.',
    })
  })

  it('keeps production evidence distinct and forwards the actor authorization', async () => {
    apiGet
      .mockResolvedValueOnce({ env: 'prod', items: [{ run_id: 'prod-run' }] })
      .mockResolvedValueOnce({
        env: 'prod',
        run_id: 'prod-run',
        items: [
          {
            event_type: 'RUN_SUCCESS',
            message: 'production completed',
            timestamp: '2026-09-29T12:00:00Z',
            step_key: null,
          },
        ],
      })
    const { getV1RunLogRecordsData } = await import('./logsV1')

    await expect(
      getV1RunLogRecordsData('prod', 'Bearer test-token'),
    ).resolves.toMatchObject({
      data: [
        expect.objectContaining({
          message: 'production completed',
          resource: { kind: 'run', id: 'prod-run', label: 'prod-run' },
        }),
      ],
      error: null,
    })
    expect(apiGet).toHaveBeenNthCalledWith(
      1,
      '/api/v1/runs?limit=100&env=prod',
      undefined,
      8000,
      'Bearer test-token',
    )
    expect(apiGet).toHaveBeenNthCalledWith(
      2,
      '/api/v1/runs/prod-run/logs?limit=50&env=prod',
      undefined,
      8000,
      'Bearer test-token',
    )
  })

  it('surfaces outages and succeeds when the resource is retried', async () => {
    apiGet
      .mockRejectedValueOnce(new Error('503 Service Unavailable'))
      .mockResolvedValueOnce({
        env: 'staging',
        items: [{ run_id: 'retry-run' }],
      })
      .mockResolvedValueOnce({
        env: 'staging',
        run_id: 'retry-run',
        items: [],
      })
    const { getV1RunLogRecordsData } = await import('./logsV1')

    await expect(getV1RunLogRecordsData('staging')).resolves.toEqual({
      data: null,
      error: '503 Service Unavailable',
    })
    await expect(getV1RunLogRecordsData('staging')).resolves.toEqual({
      data: [],
      error: null,
    })
    expect(apiGet).toHaveBeenCalledTimes(3)
  })
})
