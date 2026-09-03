/**
 * Tests for the shared phlo-api server transport (phlo-api.ts).
 *
 * Plan 003B makes the transport method-symmetric: apiPost and apiPut accept
 * the same sanitized authorization input as apiGet, header construction stays
 * in request(), and a malformed credential (CR/LF) is rejected before any
 * network call.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiGet, apiPost, apiPut } from './phlo-api'

const REAL_FETCH = globalThis.fetch

afterEach(() => {
  globalThis.fetch = REAL_FETCH
  vi.unstubAllGlobals()
})

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  })
  globalThis.fetch = fetchMock as unknown as typeof fetch
  return fetchMock
}

describe('phlo-api server transport', () => {
  it('forwards a bearer credential on POST', async () => {
    const fetchMock = mockFetch(200, { ok: true })
    await apiPost(
      '/api/observatory/actions',
      { action_id: 'x' },
      5000,
      'Bearer token-1',
    )
    const [_url, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(new Headers(init.headers).get('authorization')).toBe(
      'Bearer token-1',
    )
    expect(init.body).toContain('action_id')
  })

  it('forwards a bearer credential on PUT', async () => {
    const fetchMock = mockFetch(200, { ok: true })
    await apiPut(
      '/api/observatory/dataset-workflow/config',
      { enabled: true },
      5000,
      'Bearer token-2',
    )
    const [_url, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('PUT')
    expect(new Headers(init.headers).get('authorization')).toBe(
      'Bearer token-2',
    )
  })

  it('omits the Authorization header when no credential is provided', async () => {
    const fetchMock = mockFetch(200, { ok: true })
    await apiPost('/api/observatory/query', { sql: 'select 1' }, 8000)
    const [_url, init] = fetchMock.mock.calls[0]
    expect(new Headers(init.headers).has('authorization')).toBe(false)
  })

  it('rejects a credential containing a line break before any network call', async () => {
    const fetchMock = mockFetch(200, { ok: true })
    await expect(
      apiPost(
        '/api/observatory/actions',
        { action_id: 'x' },
        5000,
        'Bearer a\r\nX-Evil: 1',
      ),
    ).rejects.toThrow('Malformed authorization')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('GET and POST use the same header construction path', async () => {
    const fetchMock = mockFetch(200, { items: [] })
    await apiGet('/api/observatory/datasets', undefined, 8000, 'Bearer token-3')
    await apiPost('/api/observatory/actions', {}, 8000, 'Bearer token-4')
    const [_getUrl, getInit] = fetchMock.mock.calls[0]
    const [_postUrl, postInit] = fetchMock.mock.calls[1]
    expect(new Headers(getInit.headers).get('authorization')).toBe(
      'Bearer token-3',
    )
    expect(new Headers(postInit.headers).get('authorization')).toBe(
      'Bearer token-4',
    )
  })
})
