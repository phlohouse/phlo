/** Binary uploads retain the same server-only authorization forwarding and response checks. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { phloApi } from './client'

vi.mock('@tanstack/react-start', () => ({
  createServerOnlyFn: (fn: unknown) => fn,
}))
vi.mock('@tanstack/react-start/server', () => ({
  getRequestHeader: (name: string) =>
    name === 'authorization' ? 'Bearer verified-session' : undefined,
}))
afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})
describe('authenticated binary forwarding', () => {
  it('forwards unchanged bytes and session authorization without JSON encoding', async () => {
    vi.stubEnv('PHLO_API_URL', 'http://api.test')
    const fetch = vi
      .fn()
      .mockResolvedValue(Response.json({ env: 'sandbox', accepted: true }))
    vi.stubGlobal('fetch', fetch)
    const body = new Uint8Array([0xef, 0xbb, 0xbf, 97, 10, 49, 10]).buffer
    await phloApi(
      'api/v1/uploads/csv?env=sandbox',
      z.object({ env: z.string(), accepted: z.boolean() }),
      {
        env: 'sandbox',
        rawBody: body,
        headers: { 'content-type': 'text/csv' },
      },
    )
    const [url, options] = fetch.mock.calls[0]
    expect(String(url)).toBe('http://api.test/api/v1/uploads/csv?env=sandbox')
    expect(options.body).toBe(body)
    expect(options.method).toBe('POST')
    expect(options.headers.get('authorization')).toBe('Bearer verified-session')
    expect(options.headers.get('content-type')).toBe('text/csv')
  })
  it('refuses ambiguous JSON plus binary content before issuing a request', async () => {
    vi.stubEnv('PHLO_API_URL', 'http://api.test')
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(
      phloApi('api/v1/uploads/csv', z.object({}), {
        body: {},
        rawBody: new ArrayBuffer(1),
      }),
    ).rejects.toThrow('either JSON or binary')
    expect(fetch).not.toHaveBeenCalled()
  })
})
