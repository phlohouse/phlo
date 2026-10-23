/** Reclaimed upload links recover to scoped retained history without hiding other failures. */
import { afterEach, expect, it, vi } from 'vitest'
import { isRedirect } from '@tanstack/react-router'
import { Route } from './_app/sources'
import type * as SourcesApi from '@/lib/data/api/sources'
import type * as ClientApi from '@/lib/data/api/client'
import { PhloApiError } from '@/lib/data/api/client'

const api = vi.hoisted(() => ({ sources: vi.fn(), upload: vi.fn() }))
vi.mock('@/lib/data/api/sources', async (original) => ({
  ...(await original<typeof SourcesApi>()),
  getSources: api.sources,
}))
vi.mock('@/lib/data/api/client', async (original) => ({
  ...(await original<typeof ClientApi>()),
  phloApi: api.upload,
}))
// Execute server handlers locally; production browser verification covers transport redirects.
vi.mock('@tanstack/react-start', () => ({
  createServerOnlyFn: (fn: unknown) => fn,
  createServerFn: () => {
    const chain = {
      inputValidator: (_schema: unknown) => chain,
      handler: (handler: (input: { data: unknown }) => unknown) => handler,
    }
    return chain
  },
}))
afterEach(() => vi.resetAllMocks())

function load() {
  api.sources.mockResolvedValue({ env: 'qa', items: [], next_cursor: null })
  const loader = Route.options.loader
  if (typeof loader !== 'function') throw new Error('Sources loader is missing')
  return loader({
    deps: { env: 'qa', tab: 'upload', upload: 'a'.repeat(32) },
  } as Parameters<typeof loader>[0])
}

it.each(['started', 'completed'])(
  'redirects %s CSV reclamation to same-env archived history and removes the detail selection',
  async (state) => {
    api.upload.mockRejectedValue(new PhloApiError(410, `Reclamation ${state}`))
    const failure = await load().catch((error: unknown) => error)
    expect(isRedirect(failure)).toBe(true)
    expect(failure).toMatchObject({
      options: {
        to: '/sources',
        search: { env: 'qa', tab: 'retained', archived: true },
      },
    })
    if (!isRedirect(failure)) throw new Error('Expected a router redirect')
    expect(failure.options.search).not.toHaveProperty('upload')
  },
)

it.each([401, 403, 404, 409, 503])(
  'propagates non-reclamation HTTP%s without redirecting',
  async (status) => {
    const error = new PhloApiError(status)
    api.upload.mockRejectedValue(error)
    await expect(load()).rejects.toBe(error)
  },
)

it('does not mistake arbitrary failures or a message-only410 for a reclaimed resource', async () => {
  const error = new Error('Phlo API request failed (410).')
  api.upload.mockRejectedValue(error)
  await expect(load()).rejects.toBe(error)
})

it('keeps available upload detail rather than redirecting', async () => {
  const upload = { upload_id: 'a'.repeat(32), filename: 'retained.csv' }
  api.upload.mockResolvedValue(upload)
  await expect(load()).resolves.toMatchObject({ env: 'qa', upload })
})
