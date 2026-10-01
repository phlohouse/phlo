import { beforeEach, describe, expect, it, vi } from 'vitest'

type Middleware = (options: {
  next: (options?: { context?: Record<string, unknown> }) => unknown
  request: Request
}) => unknown

type ServerFnOptions = { data?: unknown; headers?: HeadersInit }

const apiGet = vi.fn()
const apiPut = vi.fn()

vi.mock('@tanstack/react-start', () => ({
  createMiddleware: () => ({ server: <T>(handler: T): T => handler }),
  createServerFn: () => {
    const middlewares: Array<Middleware> = []
    let validate = (value: unknown): unknown => value
    const builder = {
      middleware: (registered: Array<Middleware>) => {
        middlewares.push(...registered)
        return builder
      },
      inputValidator: (validator: (value: unknown) => unknown) => {
        validate = validator
        return builder
      },
      handler: <T>(handler: T): T => {
        const invoke = ((options: ServerFnOptions = {}) => {
          let context: Record<string, unknown> = {}
          const next = (index: number): unknown => {
            if (index === middlewares.length) {
              return (
                handler as unknown as (input: {
                  context: Record<string, unknown>
                  data: unknown
                }) => unknown
              )({ context, data: validate(options.data) })
            }
            return middlewares[index]({
              request: new Request('https://observatory.test', {
                headers: options.headers,
              }),
              next: (nextOptions = {}) => {
                context = { ...context, ...nextOptions.context }
                return next(index + 1)
              },
            })
          }
          return next(0)
        }) as unknown as T
        return invoke
      },
    }
    return builder
  },
}))

vi.mock('@/server/phlo-api', () => ({ apiGet, apiPut }))

describe('canonical settings v1 clients', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPut.mockReset()
  })

  it('reads durable settings from the v1 admin endpoint', async () => {
    apiGet.mockResolvedValue({ version: 4, values: { density: 'compact' } })
    const { getAdminSettings } = await import('./adminSettings')

    await expect(getAdminSettings()).resolves.toEqual({
      data: { version: 4, values: { density: 'compact' } },
      error: null,
    })
    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/admin/settings',
      undefined,
      30_000,
      undefined,
    )
  })

  it('forwards the signed-in caller for admin settings authorization', async () => {
    apiGet.mockResolvedValue({ version: 0, values: {} })
    const { getAdminSettings } = await import('./adminSettings')

    await getAdminSettings({
      headers: { authorization: 'Bearer caller-token' },
    })
    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/admin/settings',
      undefined,
      30_000,
      'Bearer caller-token',
    )
  })

  it('writes the versioned v1 payload with the caller bearer credential', async () => {
    apiPut.mockResolvedValue({ version: 5, values: { density: 'compact' } })
    const { putAdminSettings } = await import('./adminSettings')

    await expect(
      putAdminSettings({
        data: { expectedVersion: 4, values: { density: 'compact' } },
        headers: { authorization: 'Bearer caller-token' },
      }),
    ).resolves.toEqual({
      data: { version: 5, values: { density: 'compact' } },
      error: null,
    })
    expect(apiPut).toHaveBeenCalledWith(
      '/api/v1/admin/settings',
      { expected_version: 4, values: { density: 'compact' } },
      30_000,
      'Bearer caller-token',
    )
  })

  it('reads the authenticated identity through /api/v1/me', async () => {
    apiGet.mockResolvedValue({
      subject: 'alice',
      principal_type: 'user',
      email: 'alice@example.test',
      roles: ['admin'],
      permissions: { prod: ['settings.manage'] },
    })
    const { getCurrentIdentity } = await import('./settingsIdentity')

    await expect(
      getCurrentIdentity({ headers: { authorization: 'Bearer caller-token' } }),
    ).resolves.toMatchObject({ data: { subject: 'alice', roles: ['admin'] } })
    expect(apiGet).toHaveBeenCalledWith(
      '/api/v1/me',
      undefined,
      30_000,
      'Bearer caller-token',
    )
  })
})
