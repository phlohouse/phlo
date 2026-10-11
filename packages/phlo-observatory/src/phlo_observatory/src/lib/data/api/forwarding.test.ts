/** Exercises the Node transport against a real HTTP boundary with request-scoped identity. */
import { createServer } from 'node:http'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { phloApi, overviewSchema } from './client'

const requestHeaders = vi.hoisted(() => new Map<string, string>())
vi.mock('@tanstack/react-start', () => ({
  createServerOnlyFn: (fn: unknown) => fn,
}))
vi.mock('@tanstack/react-start/server', () => ({
  getRequestHeader: (name: string) => requestHeaders.get(name),
}))
afterEach(() => {
  requestHeaders.clear()
  vi.unstubAllEnvs()
})

describe('authenticated Node forwarding', () => {
  it('uses each caller identity and exact mutation intent, never a configured shared token', async () => {
    const received: Array<{
      authorization?: string
      path?: string
      method?: string
      body: string
    }> = []
    const server = createServer(async (req, res) => {
      let body = ''
      for await (const chunk of req) body += chunk
      received.push({
        authorization: req.headers.authorization,
        path: req.url,
        method: req.method,
        body,
      })
      res.setHeader('content-type', 'application/json')
      res.end(JSON.stringify({ env: 'staging', accepted: true }))
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    if (!address || typeof address === 'string')
      throw new Error('No HTTP listener')
    vi.stubEnv('PHLO_API_URL', `http://127.0.0.1:${address.port}`)
    vi.stubEnv('PHLO_API_TOKEN', 'shared-token-must-not-be-used')
    const schema = z.object({
      env: z.literal('staging'),
      accepted: z.boolean(),
    })
    try {
      requestHeaders.set('authorization', 'Bearer alice')
      requestHeaders.set('x-auth-request-access-token', 'proxy-token')
      await expect(
        phloApi('/api/v1/queries?env=staging', schema, {
          env: 'staging',
          method: 'POST',
          body: { sql: 'SELECT 7' },
          idempotencyKey: 'once',
        }),
      ).resolves.toEqual({ env: 'staging', accepted: true })
      requestHeaders.delete('authorization')
      await phloApi('/api/v1/overview?env=staging', schema, { env: 'staging' })
      requestHeaders.clear()
      await phloApi('/api/v1/overview?env=staging', schema)
      expect(received).toEqual([
        {
          authorization: 'Bearer alice',
          path: '/api/v1/queries?env=staging',
          method: 'POST',
          body: '{"sql":"SELECT 7"}',
        },
        {
          authorization: 'Bearer proxy-token',
          path: '/api/v1/overview?env=staging',
          method: 'GET',
          body: '',
        },
        {
          authorization: undefined,
          path: '/api/v1/overview?env=staging',
          method: 'GET',
          body: '',
        },
      ])
    } finally {
      await new Promise<void>((resolve, reject) =>
        server.close((err) => (err ? reject(err) : resolve())),
      )
    }
  })

  it('preserves permission/backend failures and rejects cross-environment payloads at the actual transport', async () => {
    vi.stubEnv('PHLO_API_URL', 'http://api.invalid')
    const fetch = vi.spyOn(globalThis, 'fetch')
    try {
      for (const status of [401, 403, 503]) {
        fetch.mockResolvedValueOnce(
          Response.json({ detail: 'private' }, { status }),
        )
        await expect(
          phloApi('api/v1/overview?env=staging', overviewSchema, {
            env: 'staging',
          }),
        ).rejects.toMatchObject({ status, name: 'PhloApiError' })
      }
      fetch.mockResolvedValueOnce(Response.json({ env: 'prod' }))
      await expect(
        phloApi('api/v1/overview?env=staging', z.object({ env: z.string() }), {
          env: 'staging',
        }),
      ).rejects.toThrow('different environment')
      fetch.mockRejectedValueOnce(new Error('private network detail'))
      await expect(phloApi('api/v1/overview', overviewSchema)).rejects.toThrow(
        'Phlo API is unreachable.',
      )
    } finally {
      fetch.mockRestore()
    }
  })
})
