/**
 * Tests the guarded run-action clients: both clients route through the
 * authenticated-mutation middleware, post to the guarded endpoints, always
 * submit dry_run=false for retry with the mandatory
 * idempotency key, and surface transport failures as safe error results.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

const guardedResult = {
  contract_version: 1,
  action_kind: 'run.retry',
  status: 'accepted',
  verification_handle: 'vh-abc123',
  target: { run_id: 'finance/daily-orders' },
  message: '',
}

describe('guarded run-action clients', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
  })

  it('retries through the guarded authenticated endpoint with dry_run=false', async () => {
    apiPost.mockResolvedValue(guardedResult)
    const { retryObservatoryRun } = await import('./runActions')

    const result = await retryObservatoryRun({
      data: {
        idempotencyKey: 'run-action-key-1',
        projectId: 'finance',
        runId: 'finance/daily-orders',
      },
      headers: { authorization: 'Bearer token.test' },
    })

    expect(result.error).toBeNull()
    expect(result.data).toEqual(guardedResult)
    expect(apiPost).toHaveBeenCalledWith(
      '/api/observatory/runs/finance%2Fdaily-orders/retry',
      {
        dry_run: false,
        idempotency_key: 'run-action-key-1',
        project_id: 'finance',
      },
      130_000,
      'Bearer token.test',
    )
  })

  it('cancels through the guarded authenticated endpoint without dry_run', async () => {
    apiPost.mockResolvedValue({
      ...guardedResult,
      action_kind: 'run.cancel',
      status: 'reconciled',
    })
    const { cancelObservatoryRun } = await import('./runActions')

    const result = await cancelObservatoryRun({
      data: { idempotencyKey: 'run-action-key-2', runId: 'ops/nightly' },
      headers: { authorization: 'Bearer token.test' },
    })

    expect(result.error).toBeNull()
    expect(result.data?.status).toBe('reconciled')
    expect(apiPost).toHaveBeenCalledWith(
      '/api/observatory/runs/ops%2Fnightly/cancel',
      { idempotency_key: 'run-action-key-2', project_id: null },
      130_000,
      'Bearer token.test',
    )
  })

  it('renders transport failures as safe error results', async () => {
    apiPost.mockRejectedValue(new Error('phlo-api error: 401 Unauthorized'))
    const { retryObservatoryRun } = await import('./runActions')

    const result = await retryObservatoryRun({
      data: { idempotencyKey: 'run-action-key-3', runId: 'ops/nightly' },
    })

    expect(result.data).toBeNull()
    expect(result.error).toBe('phlo-api error: 401 Unauthorized')
  })

  it('generates non-blank idempotency keys', async () => {
    const { newRunActionIdempotencyKey } = await import('./runActions')
    const key = newRunActionIdempotencyKey()
    expect(key.startsWith('run-action-')).toBe(true)
    expect(key.trim().length).toBeGreaterThan('run-action-'.length)
    expect(newRunActionIdempotencyKey()).not.toBe(key)
  })
})
