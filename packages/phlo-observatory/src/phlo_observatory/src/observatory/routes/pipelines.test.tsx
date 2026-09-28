// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Pipelines } from '@/routes/pipelines'

const clients = vi.hoisted(() => ({
  getV1PipelineSnapshot: vi.fn(),
  launchV1Job: vi.fn(),
  newV1PipelineIdempotencyKey: () => 'pipeline-key',
  pauseV1Schedule: vi.fn(),
  resumeV1Schedule: vi.fn(),
}))

vi.mock('@/observatory/api/pipelinesV1', () => clients)
const environment = vi.hoisted(() => ({
  current: null as 'prod' | 'staging' | null,
}))
vi.mock('@/observatory/api/environment', () => ({
  environmentChangeEvent: () => 'environment-change',
  selectedEnvironment: () => environment.current,
}))
vi.mock('@/observatory/routes/liveResource', () => ({
  useLiveResource: (read: () => unknown) => ({
    data: snapshot,
    error: null,
    isLoading: false,
    read,
  }),
}))
vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => () => ({}),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))

let snapshot: unknown = null

afterEach(() => {
  cleanup()
  environment.current = null
  vi.clearAllMocks()
})

describe('Pipelines', () => {
  it('requires an explicit environment instead of using fixture data', () => {
    render(<Pipelines />)
    expect(screen.getByText(/No environment is assumed/)).toBeTruthy()
  })

  it('renders v1 job, schedule, and run evidence', () => {
    environment.current = 'prod'
    snapshot = [
      {
        jobs: {
          items: [
            {
              id: 'orders',
              repository_name: 'warehouse',
              description: null,
              selected_assets: [['gold', 'orders']],
            },
          ],
        },
        schedules: {
          items: [{ id: 'daily', job_id: 'orders', status: 'RUNNING' }],
        },
        runs: {
          items: [
            {
              run_id: 'run-1',
              job_id: 'orders',
              status: 'SUCCESS',
              created_at: '2026-01-01T00:00:00Z',
              started_at: null,
              ended_at: null,
              duration_seconds: null,
            },
          ],
        },
      },
    ]
    render(<Pipelines />)
    expect(screen.getByText('orders')).toBeTruthy()
    expect(screen.getByText(/Latest observed run: run-1/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Pause daily' })).toBeTruthy()
  })

  it('submits schedule actions with an environment and idempotency key', () => {
    environment.current = 'staging'
    snapshot = [
      {
        jobs: {
          items: [
            {
              id: 'orders',
              repository_name: 'warehouse',
              description: 'Orders',
              selected_assets: [],
            },
          ],
        },
        schedules: {
          items: [{ id: 'daily', job_id: 'orders', status: 'RUNNING' }],
        },
        runs: { items: [] },
      },
    ]
    clients.pauseV1Schedule.mockResolvedValue({
      data: { status: 'accepted' },
      error: null,
    })
    render(<Pipelines />)
    fireEvent.click(screen.getByRole('button', { name: 'Pause daily' }))
    expect(clients.pauseV1Schedule).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm action' }))
    expect(clients.pauseV1Schedule).toHaveBeenCalledWith({
      data: {
        environment: 'staging',
        id: 'daily',
        idempotencyKey: 'pipeline-key',
      },
    })
  })
})
