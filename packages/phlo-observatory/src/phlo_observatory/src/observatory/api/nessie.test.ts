// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { selectEnvironment } from './environment'
import { checkNessieConnection, getBranches } from './nessie'

const { apiGet, withCache } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  withCache: vi.fn(<T>(loader: () => Promise<T>) => loader()),
}))

vi.mock('@tanstack/react-start', () => ({
  createMiddleware: () => ({
    inputValidator: () => ({ server: (handler: unknown) => handler }),
    server: (handler: unknown) => handler,
  }),
  createServerFn: () => {
    const builder = {
      middleware: () => builder,
      inputValidator: () => builder,
      handler:
        (handler: (options: { context: {}; data: unknown }) => unknown) =>
        ({ data }: { data: unknown }) =>
          handler({ context: {}, data }),
    }
    return builder
  },
}))

vi.mock('@/server/cache', () => ({
  cacheKeys: {
    nessieBranches: () => 'nessie:branches',
    nessieConnection: () => 'nessie:connection',
  },
  cacheTTL: { nessieBranches: 1, nessieConnection: 1 },
  withCache,
}))

vi.mock('@/server/phlo-api', () => ({ apiGet }))

afterEach(() => {
  apiGet.mockReset()
  withCache.mockClear()
})

describe('Nessie branch reads', () => {
  it('fails closed when no environment has been selected', async () => {
    await expect(
      checkNessieConnection({ data: { env: undefined } }),
    ).resolves.toEqual({
      connected: false,
      error: expect.stringContaining('Select prod or staging'),
    })
    expect(apiGet).not.toHaveBeenCalled()
  })

  it('uses the selected v1 environment and preserves the returned head hash', async () => {
    selectEnvironment('staging')
    apiGet.mockResolvedValue({
      env: 'staging',
      items: [
        {
          name: 'staging-main',
          type: 'BRANCH',
          hash: 'verified-head',
          protected: true,
        },
      ],
    })

    await expect(getBranches({ data: { env: 'staging' } })).resolves.toEqual([
      { name: 'staging-main', type: 'BRANCH', hash: 'verified-head' },
    ])
    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/branches?env=staging',
      undefined,
      30000,
      undefined,
    )
    expect(withCache).toHaveBeenCalledWith(
      expect.any(Function),
      'nessie:branches:/api/v1/branches?env=staging',
      1,
    )
  })
})
