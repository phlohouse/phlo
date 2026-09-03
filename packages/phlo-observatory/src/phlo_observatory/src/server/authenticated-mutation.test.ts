/**
 * Tests for the shared inbound mutation-authorization middleware
 * (authenticated-mutation.ts).
 *
 * Covers the Plan 003B transport matrix: valid bearer credential forwarded,
 * missing credential dropped (API fails closed), unsupported schemes dropped,
 * hostile Origin rejected before the handler, and same-origin/non-browser
 * requests trusted.
 */
import { describe, expect, it, vi } from 'vitest'

import {
  mutationAuthorization,
  mutationBearerAuthorization,
  trustedSameOrigin,
} from './authenticated-mutation'

vi.mock('@tanstack/react-start', () => ({
  createMiddleware: () => ({
    server: (handler: unknown) => handler,
  }),
}))

function requestWith(headers: Record<string, string>): Request {
  return new Request('https://observatory.example.test/api/x', { headers })
}

function invoke(
  handler: (options: Record<string, unknown>) => unknown,
  request: Request,
) {
  let context: Record<string, unknown> | undefined
  const result = handler({
    request,
    next: (nextOptions: { context?: Record<string, unknown> } = {}) => {
      context = nextOptions.context
      return 'next-ran'
    },
  })
  return { result, context }
}

describe('mutationBearerAuthorization', () => {
  it('accepts a well-formed bearer credential', () => {
    expect(mutationBearerAuthorization('Bearer abc.def.ghi')).toBe(
      'Bearer abc.def.ghi',
    )
  })

  it('returns undefined for a missing header', () => {
    expect(mutationBearerAuthorization(null)).toBeUndefined()
  })

  it('returns undefined for unsupported schemes', () => {
    expect(mutationBearerAuthorization('Basic dXNlcjpwYXNz')).toBeUndefined()
    expect(mutationBearerAuthorization('Token abc')).toBeUndefined()
  })

  it('returns undefined for a bare/malformed bearer value', () => {
    expect(mutationBearerAuthorization('Bearer ')).toBeUndefined()
    expect(mutationBearerAuthorization('Bearer')).toBeUndefined()
  })
})

describe('trustedSameOrigin', () => {
  it('trusts a matching origin and host', () => {
    const request = requestWith({
      origin: 'https://observatory.example.test',
      host: 'observatory.example.test',
    })
    expect(trustedSameOrigin(request)).toBe(true)
  })

  it('rejects a cross-site origin', () => {
    const request = requestWith({
      origin: 'https://evil.example',
      host: 'observatory.example.test',
    })
    expect(trustedSameOrigin(request)).toBe(false)
  })

  it('trusts a non-browser request with no Origin header', () => {
    const request = requestWith({ host: 'observatory.example.test' })
    expect(trustedSameOrigin(request)).toBe(true)
  })

  it('rejects a malformed Origin value', () => {
    const request = requestWith({
      origin: 'not-a-url',
      host: 'observatory.example.test',
    })
    expect(trustedSameOrigin(request)).toBe(false)
  })
})

describe('mutationAuthorization middleware', () => {
  // With @tanstack/react-start mocked, createMiddleware(...).server(handler)
  // returns the handler, so mutationAuthorization is directly invocable.
  const serverHandler = mutationAuthorization as unknown as (
    o: Record<string, unknown>,
  ) => unknown

  it('forwards a sanitized bearer credential into the request context', () => {
    const { result, context } = invoke(
      serverHandler,
      requestWith({
        authorization: 'Bearer real.jwt.token',
        host: 'observatory.example.test',
      }),
    )
    expect(result).toBe('next-ran')
    expect(context).toEqual({ authorization: 'Bearer real.jwt.token' })
  })

  it('drops a missing credential so the API can fail closed', () => {
    const { result, context } = invoke(
      serverHandler,
      requestWith({ host: 'observatory.example.test' }),
    )
    expect(result).toBe('next-ran')
    expect(context).toEqual({ authorization: undefined })
  })

  it('drops an unsupported scheme', () => {
    const { result, context } = invoke(
      serverHandler,
      requestWith({
        authorization: 'Basic dXNlcjpwYXNz',
        host: 'observatory.example.test',
      }),
    )
    expect(result).toBe('next-ran')
    expect(context).toEqual({ authorization: undefined })
  })

  it('rejects a cross-site mutation request before the handler', () => {
    expect(() =>
      invoke(
        serverHandler,
        requestWith({
          origin: 'https://evil.example',
          host: 'observatory.example.test',
        }),
      ),
    ).toThrow('Cross-site mutation request rejected')
  })
})
