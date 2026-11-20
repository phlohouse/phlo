/** A viewer who may read notebooks but not run them still gets the workspace. */
import { describe, expect, it, vi } from 'vitest'
import { getNotebookWorkspace } from './notebooks'
import { PhloApiError, phloApi } from './client'

vi.mock('@tanstack/react-start', () => ({
  createServerFn: () => ({
    inputValidator: () => ({ handler: (fn: unknown) => fn }),
  }),
}))
vi.mock('./client', async () => {
  const { z } = await import('zod')
  class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly detail?: string,
      readonly reasonCode?: string,
    ) {
      super(detail)
    }
  }
  return {
    PhloApiError: ApiError,
    environmentSchema: z.string(),
    phloApi: vi.fn(),
  }
})

describe('notebook workspace', () => {
  it('loads with no kernels when the kernel list is forbidden', async () => {
    vi.mocked(phloApi).mockImplementation((path: string) => {
      if (path.startsWith('/api/v1/notebooks/kernels'))
        return Promise.reject(
          new PhloApiError(403, 'Not allowed.', 'forbidden'),
        )
      return Promise.resolve({ env: 'dev', items: [] })
    })
    const workspace = await getNotebookWorkspace({ data: { env: 'dev' } })
    expect(workspace.kernels).toEqual({ state: 'available', items: [] })
    expect(workspace.notebook).toEqual({ state: 'none' })
  })
  it('still fails on any other kernel error', async () => {
    vi.mocked(phloApi).mockImplementation((path: string) => {
      if (path.startsWith('/api/v1/notebooks/kernels'))
        return Promise.reject(new PhloApiError(500, 'Broken.'))
      return Promise.resolve({ env: 'dev', items: [] })
    })
    await expect(
      getNotebookWorkspace({ data: { env: 'dev' } }),
    ).rejects.toThrow('Broken.')
  })
})
