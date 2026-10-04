// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getBranchComparison, getBranchReferences } from './branches'

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }))
vi.mock('@tanstack/react-start', () => ({
  createMiddleware: () => {
    const middleware = {
      inputValidator: () => middleware,
      server: (handler: unknown) => handler,
    }
    return middleware
  },
  createServerFn: () => {
    const builder = {
      middleware: () => builder,
      inputValidator: () => builder,
      handler:
        (handler: (value: { context: {}; data: unknown }) => unknown) =>
        ({ data }: { data: unknown }) =>
          handler({ context: {}, data }),
    }
    return builder
  },
}))
vi.mock('@/server/phlo-api', () => ({ apiGet }))

afterEach(() => apiGet.mockReset())

describe('v1 branch review client', () => {
  it('retains branch and tag references from the environment-scoped contract', async () => {
    apiGet.mockResolvedValue({
      env: 'prod',
      items: [
        {
          env: 'prod',
          name: 'prod-main',
          type: 'BRANCH',
          hash: 'head',
          protected: true,
        },
        {
          env: 'prod',
          name: 'prod-tag',
          type: 'TAG',
          hash: 'tag',
          protected: false,
        },
      ],
    })
    await expect(
      getBranchReferences({ data: { env: 'prod' } }),
    ).resolves.toEqual({
      kind: 'available',
      data: [
        expect.objectContaining({ name: 'prod-main' }),
        expect.objectContaining({ name: 'prod-tag', type: 'TAG' }),
      ],
    })
    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/branches/refs?env=prod',
      undefined,
      8000,
      undefined,
    )
  })

  it('reports invalid or unavailable comparison data explicitly', async () => {
    apiGet.mockResolvedValue({ env: 'prod', status: 'compared' })
    await expect(
      getBranchComparison({
        data: { env: 'prod', branch: 'prod-feature', target: 'prod-main' },
      }),
    ).resolves.toMatchObject({ kind: 'unavailable' })
  })

  it('rejects valid-shaped references from a different environment', async () => {
    apiGet.mockResolvedValue({
      env: 'staging',
      items: [],
    })
    await expect(
      getBranchReferences({ data: { env: 'prod' } }),
    ).resolves.toMatchObject({ kind: 'unavailable' })
  })

  it('rejects a comparison for another requested branch', async () => {
    apiGet.mockResolvedValue({
      env: 'prod',
      source: 'other-branch',
      target: 'prod-main',
      source_hash: 'source',
      target_hash: 'target',
      merge_base: null,
      ahead: 1,
      behind: 0,
      status: 'compared',
    })
    await expect(
      getBranchComparison({
        data: { env: 'prod', branch: 'requested-branch', target: 'prod-main' },
      }),
    ).resolves.toMatchObject({ kind: 'unavailable' })
  })
})
