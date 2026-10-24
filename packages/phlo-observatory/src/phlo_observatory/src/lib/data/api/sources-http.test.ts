/** Source inventory uses each API's real selectors, including env-only job discovery. */
import { describe, expect, it, vi } from 'vitest'
import { getSources } from './sources'
import { phloApi } from './client'

vi.mock('@tanstack/react-start', () => ({
  createServerFn: () => ({
    inputValidator: () => ({ handler: (fn: unknown) => fn }),
  }),
}))
vi.mock('./client', async () => {
  const { z } = await import('zod')
  return {
    environmentSchema: z.string(),
    phloApi: vi.fn(),
    optionalRead: async (request: Promise<unknown>) => ({
      state: 'available',
      data: await request,
    }),
  }
})
describe('source discovery request selectors', () => {
  it('uses paged source discovery and env-only bounded job discovery', async () => {
    vi.mocked(phloApi).mockResolvedValue({
      env: 'sandbox',
      items: [],
      next_cursor: null,
    })
    const result = await getSources({ data: { env: 'sandbox' } })
    expect(result.env).toBe('sandbox')
    expect(vi.mocked(phloApi).mock.calls.map(([path]) => path)).toEqual([
      'api/v1/sources?env=sandbox&limit=100',
      'api/v1/jobs?env=sandbox',
    ])
  })
})
