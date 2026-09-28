import { describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
vi.mock('@/server/phlo-api', () => ({ apiGet, apiPost: vi.fn() }))

describe('v1 pipelines client', () => {
  it('reads jobs, schedules, and bounded runs in the explicit environment', async () => {
    apiGet
      .mockResolvedValueOnce({ env: 'prod', items: [] })
      .mockResolvedValueOnce({ env: 'prod', items: [] })
      .mockResolvedValueOnce({ env: 'prod', items: [] })
    const { getV1PipelineSnapshotData } = await import('./pipelinesV1')
    await getV1PipelineSnapshotData('prod')
    expect(apiGet).toHaveBeenNthCalledWith(
      1,
      '/api/v1/jobs',
      { env: 'prod' },
      8000,
    )
    expect(apiGet).toHaveBeenNthCalledWith(
      2,
      '/api/v1/schedules',
      { env: 'prod' },
      8000,
    )
    expect(apiGet).toHaveBeenNthCalledWith(
      3,
      '/api/v1/runs',
      { env: 'prod', limit: 100 },
      8000,
    )
  })

  it('makes invalid backend payloads explicitly unavailable', async () => {
    apiGet.mockResolvedValue({ items: [{ id: 42 }] })
    const { getV1PipelineSnapshotData } = await import('./pipelinesV1')
    await expect(getV1PipelineSnapshotData('prod')).resolves.toMatchObject({
      data: null,
      error: expect.any(String),
    })
  })

  it('rejects otherwise valid data returned for a different environment', async () => {
    apiGet
      .mockResolvedValueOnce({ env: 'staging', items: [] })
      .mockResolvedValueOnce({ env: 'prod', items: [] })
      .mockResolvedValueOnce({ env: 'prod', items: [] })
    const { getV1PipelineSnapshotData } = await import('./pipelinesV1')

    await expect(getV1PipelineSnapshotData('prod')).resolves.toMatchObject({
      data: null,
      error: expect.stringContaining('another environment'),
    })
  })

  it('validates the complete v1 item shapes', async () => {
    apiGet
      .mockResolvedValueOnce({
        env: 'staging',
        items: [
          {
            id: 'daily-orders',
            repository_name: 'analytics',
            description: null,
            selected_assets: [],
            assets_url: '/api/v1/jobs/daily-orders/assets',
            runs_url: '/api/v1/jobs/daily-orders/runs',
            incidents_url: '/api/v1/jobs/daily-orders/incidents',
            resource_id: 'job:daily-orders',
          },
        ],
      })
      .mockResolvedValueOnce({
        env: 'staging',
        items: [
          {
            id: 'daily-orders-schedule',
            job_id: 'daily-orders',
            status: 'RUNNING',
            resource_id: 'schedule:daily-orders',
          },
        ],
      })
      .mockResolvedValueOnce({
        env: 'staging',
        items: [
          {
            run_id: 'run-1',
            job_id: 'daily-orders',
            status: 'SUCCESS',
            created_at: '2026-09-28T12:00:00Z',
            started_at: null,
            ended_at: null,
            duration_seconds: null,
            selected_assets: [],
            logs_url: '/api/v1/runs/run-1/logs',
            resource_id: 'run:run-1',
          },
        ],
      })
    const { getV1PipelineSnapshotData } = await import('./pipelinesV1')

    await expect(getV1PipelineSnapshotData('staging')).resolves.toMatchObject({
      error: null,
      data: {
        jobs: { items: [expect.objectContaining({ id: 'daily-orders' })] },
      },
    })
  })
})
