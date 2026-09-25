/**
 * Tests for Observatory server-function authentication.
 *
 * Covers the environment predicate (the same production-like spellings as
 * requires_http_authorization()), the fail-closed startup configuration
 * check, and authMiddleware's token validation under enabled and opt-in
 * configurations.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  assertObservatoryAuthConfiguration,
  authMiddleware,
  isAuthEnabled,
  isProductionLikeEnvironment,
} from './auth'

vi.mock('@tanstack/react-start', () => ({
  createMiddleware: () => ({
    inputValidator: () => ({
      server: (handler: unknown) => handler,
    }),
  }),
}))

const middlewareHandler = authMiddleware as unknown as (options: {
  next: (nextOptions?: { context?: Record<string, unknown> }) => unknown
  data: { authToken?: string }
}) => unknown

async function runMiddleware(authToken?: string) {
  let context: Record<string, unknown> | undefined
  const result = await middlewareHandler({
    data: { authToken },
    next: (nextOptions = {}) => {
      context = nextOptions.context
      return 'next-ran'
    },
  })
  return { result, context }
}

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('isProductionLikeEnvironment', () => {
  it.each(['prod', 'production', 'staging', 'regulated', ' PRODUCTION '])(
    'is true for production-like PHLO_ENVIRONMENT=%j',
    (environment) => {
      vi.stubEnv('PHLO_ENVIRONMENT', environment)
      expect(isProductionLikeEnvironment()).toBe(true)
    },
  )

  it.each(['development', 'test', 'dev', ''])(
    'is false for non-production PHLO_ENVIRONMENT=%j',
    (environment) => {
      vi.stubEnv('PHLO_ENVIRONMENT', environment)
      vi.stubEnv('PHLO_REGULATED', undefined)
      expect(isProductionLikeEnvironment()).toBe(false)
    },
  )

  it('is false when PHLO_ENVIRONMENT is unset', () => {
    vi.stubEnv('PHLO_ENVIRONMENT', undefined)
    vi.stubEnv('PHLO_REGULATED', undefined)
    expect(isProductionLikeEnvironment()).toBe(false)
  })

  it.each(['1', 'true', 'yes', 'on', 'TRUE'])(
    'is true for PHLO_REGULATED=%j',
    (regulated) => {
      vi.stubEnv('PHLO_REGULATED', regulated)
      vi.stubEnv('PHLO_ENVIRONMENT', undefined)
      expect(isProductionLikeEnvironment()).toBe(true)
    },
  )

  it('is false for PHLO_REGULATED=false', () => {
    vi.stubEnv('PHLO_REGULATED', 'false')
    vi.stubEnv('PHLO_ENVIRONMENT', undefined)
    expect(isProductionLikeEnvironment()).toBe(false)
  })
})

describe('isAuthEnabled', () => {
  it('is enabled by explicit OBSERVATORY_AUTH_ENABLED=true', () => {
    vi.stubEnv('OBSERVATORY_AUTH_ENABLED', 'true')
    vi.stubEnv('PHLO_ENVIRONMENT', undefined)
    vi.stubEnv('PHLO_REGULATED', undefined)
    expect(isAuthEnabled()).toBe(true)
  })

  it('is enabled by a production-like environment without the flag', () => {
    vi.stubEnv('PHLO_ENVIRONMENT', 'production')
    vi.stubEnv('OBSERVATORY_AUTH_ENABLED', undefined)
    expect(isAuthEnabled()).toBe(true)
  })

  it('stays opt-in for local development without the flag', () => {
    vi.stubEnv('PHLO_ENVIRONMENT', undefined)
    vi.stubEnv('PHLO_REGULATED', undefined)
    vi.stubEnv('OBSERVATORY_AUTH_ENABLED', undefined)
    expect(isAuthEnabled()).toBe(false)
  })
})

describe('assertObservatoryAuthConfiguration', () => {
  it('fails startup with an actionable error when production-like and tokenless', () => {
    vi.stubEnv('PHLO_ENVIRONMENT', 'production')
    vi.stubEnv('OBSERVATORY_AUTH_TOKEN', undefined)
    expect(() => assertObservatoryAuthConfiguration()).toThrow(
      /OBSERVATORY_AUTH_TOKEN/,
    )
  })

  it('fails startup when PHLO_REGULATED is enabled without a token', () => {
    vi.stubEnv('PHLO_REGULATED', 'true')
    vi.stubEnv('OBSERVATORY_AUTH_TOKEN', undefined)
    expect(() => assertObservatoryAuthConfiguration()).toThrow(
      /OBSERVATORY_AUTH_TOKEN/,
    )
  })

  it('passes when a production-like environment configures a token', () => {
    vi.stubEnv('PHLO_ENVIRONMENT', 'production')
    vi.stubEnv('OBSERVATORY_AUTH_TOKEN', 'shared-secret')
    expect(() => assertObservatoryAuthConfiguration()).not.toThrow()
  })

  it('passes for local development without a token', () => {
    vi.stubEnv('PHLO_ENVIRONMENT', undefined)
    vi.stubEnv('PHLO_REGULATED', undefined)
    vi.stubEnv('OBSERVATORY_AUTH_TOKEN', undefined)
    expect(() => assertObservatoryAuthConfiguration()).not.toThrow()
  })
})

describe('authMiddleware', () => {
  it('rejects anonymous calls when production-like', async () => {
    vi.stubEnv('PHLO_ENVIRONMENT', 'production')
    vi.stubEnv('OBSERVATORY_AUTH_TOKEN', 'shared-secret')
    await expect(runMiddleware()).rejects.toThrow('Authentication required')
  })

  it('rejects a mismatched token when production-like', async () => {
    vi.stubEnv('PHLO_ENVIRONMENT', 'production')
    vi.stubEnv('OBSERVATORY_AUTH_TOKEN', 'shared-secret')
    await expect(runMiddleware('wrong-token')).rejects.toThrow(
      'Invalid authentication token',
    )
  })

  it('passes a matching token when production-like', async () => {
    vi.stubEnv('PHLO_ENVIRONMENT', 'production')
    vi.stubEnv('OBSERVATORY_AUTH_TOKEN', 'shared-secret')
    const { result, context } = await runMiddleware('shared-secret')
    expect(result).toBe('next-ran')
    expect(context).toEqual({ isAuthenticated: true })
  })

  it('passes without a token for local development', async () => {
    vi.stubEnv('PHLO_ENVIRONMENT', undefined)
    vi.stubEnv('PHLO_REGULATED', undefined)
    vi.stubEnv('OBSERVATORY_AUTH_ENABLED', undefined)
    vi.stubEnv('OBSERVATORY_AUTH_TOKEN', undefined)
    const { result, context } = await runMiddleware()
    expect(result).toBe('next-ran')
    expect(context).toEqual({ isAuthenticated: false })
  })
})
