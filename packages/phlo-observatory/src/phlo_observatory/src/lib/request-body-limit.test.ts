/** Server-function parsing cannot allocate an unbounded multipart or JSON body. */
import { describe, expect, it, vi } from 'vitest'
import { withServerFunctionBodyLimit } from './request-body-limit'

function streamed(chunks: Array<Uint8Array>) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  })
  const init: RequestInit & { duplex: 'half' } = {
    method: 'POST',
    body,
    duplex: 'half',
    headers: { 'content-type': 'multipart/form-data; boundary=test' },
  }
  return new Request('http://localhost/_serverFn/retain', init)
}
describe('server function body limits', () => {
  it('reads lazy framework metadata before locking the incoming stream', async () => {
    const original = streamed([new Uint8Array([97, 98, 99])])
    let metadataRead = false
    const lazyRequest = new Proxy(original, {
      get(target, property) {
        if (property === 'credentials') {
          const init: RequestInit & { duplex: 'half' } = {
            method: target.method,
            body: target.body,
            duplex: 'half',
          }
          const native = new Request(target.url, init)
          metadataRead = true
          return native.credentials
        }
        return Reflect.get(target, property, target)
      },
    })
    const response = await withServerFunctionBodyLimit(
      lazyRequest,
      async (request) => new Response(await request.text()),
      3,
    )
    expect(metadataRead).toBe(true)
    expect(await response.text()).toBe('abc')
  })

  it('accepts framework request proxies and preserves request identity and cancellation', async () => {
    const cancellation = new AbortController()
    const original = new Request(
      'http://localhost/_serverFn/save?env=sandbox',
      {
        method: 'POST',
        body: '{"sql":"select 1"}',
        headers: {
          'content-type': 'application/json',
          authorization: 'Bearer test-token',
          cookie: 'session=test-session',
        },
        signal: cancellation.signal,
        credentials: 'include',
        redirect: 'manual',
        cache: 'no-store',
      },
    )
    const frameworkRequest = new Proxy(original, {
      get(target, property) {
        if (typeof property === 'symbol') return undefined
        return Reflect.get(target, property, target)
      },
    })
    expect(() => new Request(frameworkRequest)).toThrow(/private member/)
    const response = await withServerFunctionBodyLimit(
      frameworkRequest,
      async (bounded) => {
        expect(bounded.url).toBe(original.url)
        expect(bounded.method).toBe('POST')
        expect([...bounded.headers]).toEqual([...original.headers])
        expect(bounded.credentials).toBe('include')
        expect(bounded.redirect).toBe('manual')
        expect(bounded.cache).toBe('no-store')
        expect(await bounded.text()).toBe('{"sql":"select 1"}')
        cancellation.abort()
        expect(bounded.signal.aborted).toBe(true)
        return new Response('saved')
      },
    )
    expect(await response.text()).toBe('saved')
  })

  it('rejects declared oversize requests before the parser runs', async () => {
    const parser = vi.fn()
    const response = await withServerFunctionBodyLimit(
      new Request('http://localhost/_serverFn/retain', {
        method: 'POST',
        headers: { 'content-length': '11' },
        body: '01234567890',
      }),
      parser,
      10,
    )
    expect(response.status).toBe(413)
    expect(response.headers.get('cache-control')).toBe('private, no-store')
    expect(parser).not.toHaveBeenCalled()
  })
  it('caps chunked requests without trusting content-length and converts parser errors to 413', async () => {
    const response = await withServerFunctionBodyLimit(
      streamed([new Uint8Array(6), new Uint8Array(6)]),
      async (request) => new Response(await request.arrayBuffer()),
      10,
    )
    expect(response.status).toBe(413)
    expect(response.headers.get('cache-control')).toBe('private, no-store')
  })
  it('preserves permitted bytes and unrelated request paths', async () => {
    const response = await withServerFunctionBodyLimit(
      streamed([new Uint8Array([97, 98]), new Uint8Array([99])]),
      async (request) => new Response(await request.arrayBuffer()),
      3,
    )
    expect(response.status).toBe(200)
    expect(await response.text()).toBe('abc')
    const unrelated = await withServerFunctionBodyLimit(
      new Request('http://localhost/other', { method: 'POST', body: 'larger' }),
      async (request) => new Response(await request.text()),
      3,
    )
    expect(await unrelated.text()).toBe('larger')
  })
})
