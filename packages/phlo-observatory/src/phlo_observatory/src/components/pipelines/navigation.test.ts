/** Verifies run navigation and environment scoping using the real router. */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from '@tanstack/react-router'
import type * as CoreApi from '@/lib/data/api/core'
import type * as PipelinesApi from '@/lib/data/api/pipelines'
import { routeTree } from '@/routeTree.gen'
import { getPipelineJob } from '@/lib/data/api/pipelines'

vi.mock('@/lib/data/api/core', async (importOriginal) => ({
  ...(await importOriginal<typeof CoreApi>()),
  getShell: vi.fn(() => Promise.resolve({})),
}))

vi.mock('@/lib/data/api/pipelines', async (importOriginal) => ({
  ...(await importOriginal<typeof PipelinesApi>()),
  getPipelineJob: vi.fn(() => Promise.resolve({})),
}))

function runRouter(url: string) {
  return createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [url] }),
  })
}

describe('Dedicated run navigation', () => {
  beforeEach(() => vi.clearAllMocks())

  it('loads the exact requested run in staging without loading the job page', async () => {
    const router = runRouter('/pipelines/scoped-job/runs/older-run?env=staging')
    await router.load()
    expect(router.state.matches.at(-1)?.fullPath).toBe(
      '/pipelines/$jobName/runs/$runId',
    )
    expect(getPipelineJob).toHaveBeenCalledExactlyOnceWith({
      data: { id: 'scoped-job', run: 'older-run', env: 'staging' },
    })
  })

  it('redirects a legacy run link without fetching the latest run', async () => {
    const initial = runRouter('/pipelines/scoped-job?env=staging&run=older-run')
    await initial.load()
    const normalized = initial._serverResult
    if (normalized?.type !== 'redirect')
      throw new Error('Expected search normalization.')
    const href = normalized.redirect.headers.get('Location')
    if (!href) throw new Error('Expected a redirect location.')
    const router = runRouter(href)
    await router.load()
    const result = router._serverResult
    expect(result?.type).toBe('redirect')
    if (result?.type !== 'redirect') throw new Error('Expected a run redirect.')
    expect(result.redirect.headers.get('Location')).toBe(
      '/pipelines/scoped-job/runs/older-run?env=staging',
    )
    expect(result.redirect.options).toMatchObject({
      replace: true,
    })
    expect(getPipelineJob).not.toHaveBeenCalled()
  })
})
