/**
 * Tests that Observatory's operational server functions reject anonymous
 * callers when authentication is on: service start/stop/restart, cache
 * clear/stats, and service inventory all sit behind authMiddleware, and
 * mutations additionally sit behind mutationAuthorization (hostile Origin
 * rejection plus bearer forwarding to phlo-api).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type ServerFnBuilderMock = {
  middleware: (middlewares: Array<RequestMiddleware>) => ServerFnBuilderMock
  inputValidator: <TInputValidator>(
    inputValidator: TInputValidator,
  ) => ServerFnBuilderMock
  handler: <THandler>(handler: THandler) => THandler
}

type RequestMiddleware = (options: {
  next: (options?: { context?: Record<string, unknown> }) => unknown
  request: Request
  data: unknown
}) => unknown

type ServerFnOptions = {
  data: unknown
  headers?: HeadersInit
}

const apiGet = vi.fn()
const apiPost = vi.fn()

vi.mock('@tanstack/react-start', () => ({
  createMiddleware: () => ({
    server: <THandler>(handler: THandler): THandler => handler,
    inputValidator: () => ({
      server: <THandler>(handler: THandler): THandler => handler,
    }),
  }),
  createServerFn: () => {
    const middlewares: Array<RequestMiddleware> = []
    let validateInput = (input: unknown): unknown => input
    const builder: ServerFnBuilderMock = {
      middleware: (registered) => {
        middlewares.push(...registered)
        return builder
      },
      inputValidator: (validator) => {
        validateInput = validator as (input: unknown) => unknown
        return builder
      },
      handler: (handler) =>
        ((options: ServerFnOptions) => {
          const invokeHandler = handler as unknown as (context: {
            context: Record<string, unknown>
            data: unknown
          }) => unknown
          let context: Record<string, unknown> = {}
          const invokeMiddleware = (index: number): unknown => {
            if (index === middlewares.length) {
              return invokeHandler({
                context,
                data: validateInput(options.data),
              })
            }
            return middlewares[index]({
              request: new Request('https://observatory.example.test', {
                headers: options.headers,
              }),
              data: options.data,
              next: (nextOptions = {}) => {
                context = { ...context, ...nextOptions.context }
                return invokeMiddleware(index + 1)
              },
            })
          }
          return invokeMiddleware(0)
        }) as unknown as typeof handler,
    }
    return builder
  },
}))

vi.mock('@/server/phlo-api', () => ({
  apiGet,
  apiPost,
}))

const AUTHENTICATED = { authToken: 'shared-secret' }
const TRUSTED_HOST = { host: 'observatory.example.test' }

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPost.mockResolvedValue({ status: 'succeeded' })
  vi.stubEnv('PHLO_ENVIRONMENT', 'production')
  vi.stubEnv('OBSERVATORY_AUTH_TOKEN', 'shared-secret')
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('operational server functions reject anonymous callers', () => {
  it.each(['startService', 'stopService', 'restartService'] as const)(
    '%s rejects an anonymous call',
    async (serviceFn) => {
      const module = await import('./services.server')
      await expect(
        module[serviceFn]({ data: { serviceName: 'postgres' } }),
      ).rejects.toThrow('Authentication required')
      expect(apiPost).not.toHaveBeenCalled()
    },
  )

  it.each(['getCacheStatsEndpoint', 'clearCacheEndpoint'] as const)(
    '%s rejects an anonymous call',
    async (cacheFn) => {
      const module = await import('./cache.server')
      await expect(module[cacheFn]({ data: {} })).rejects.toThrow(
        'Authentication required',
      )
    },
  )

  it.each(['getServices', 'getDockerStatus'] as const)(
    '%s rejects an anonymous call',
    async (inventoryFn) => {
      const module = await import('./services.server')
      await expect(module[inventoryFn]({ data: {} })).rejects.toThrow(
        'Authentication required',
      )
    },
  )

  it('rejects a wrong-token lifecycle call', async () => {
    const { startService } = await import('./services.server')
    await expect(
      startService({
        data: { serviceName: 'postgres', authToken: 'wrong' },
      }),
    ).rejects.toThrow('Invalid authentication token')
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('rejects a cross-site mutation before the handler', async () => {
    const { clearCacheEndpoint } = await import('./cache.server')
    expect(() =>
      clearCacheEndpoint({
        data: AUTHENTICATED,
        headers: {
          origin: 'https://evil.example',
          host: 'observatory.example.test',
        },
      }),
    ).toThrow('Cross-site mutation request rejected')
  })
})

describe('authenticated operational calls proceed', () => {
  it.each([
    ['startService', 'start'],
    ['stopService', 'stop'],
    ['restartService', 'restart'],
  ] as const)(
    '%s forwards the bearer credential to phlo-api',
    async (serviceFn, action) => {
      const module = await import('./services.server')
      const result = await module[serviceFn]({
        data: { serviceName: 'postgres', ...AUTHENTICATED },
        headers: { authorization: 'Bearer jwt.test', ...TRUSTED_HOST },
      })
      expect(result).toEqual({ success: true })
      expect(apiPost).toHaveBeenCalledWith(
        '/api/observatory/actions',
        { action_id: `postgres:${action}` },
        130000,
        'Bearer jwt.test',
      )
    },
  )

  it('clears the cache for an authenticated caller', async () => {
    const { clearCacheEndpoint } = await import('./cache.server')
    const result = await clearCacheEndpoint({
      data: AUTHENTICATED,
      headers: TRUSTED_HOST,
    })
    expect(result).toEqual({ cleared: true })
  })
})

describe('local development keeps opt-in auth', () => {
  it('passes lifecycle calls through without a token when auth is off', async () => {
    vi.stubEnv('PHLO_ENVIRONMENT', undefined)
    vi.stubEnv('PHLO_REGULATED', undefined)
    vi.stubEnv('OBSERVATORY_AUTH_ENABLED', undefined)
    const { startService } = await import('./services.server')
    const result = await startService({
      data: { serviceName: 'postgres' },
      headers: TRUSTED_HOST,
    })
    expect(result).toEqual({ success: true })
    expect(apiPost).toHaveBeenCalledWith(
      '/api/observatory/actions',
      { action_id: 'postgres:start' },
      130000,
      undefined,
    )
  })
})
