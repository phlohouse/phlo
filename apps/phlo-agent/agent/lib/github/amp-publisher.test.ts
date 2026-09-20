/** Tests for the authenticated, target-bound Amp comment publisher. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { handleAmpPublishRequest } from './amp-publisher.ts'

const publishToken = 'bridge-secret-that-is-at-least-32-bytes'

const validInput = {
  body: 'No actionable findings.',
  deliveryId: 'delivery-123',
  headSha: 'a'.repeat(40),
  kind: 'pull_request',
  labels: [],
  number: 42,
  receivedAt: '2026-09-20T10:00:00.000Z',
}

function request(input: unknown, token = publishToken): Request {
  return new Request('https://agent.example/amp/v1/github-comments', {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify(input),
  })
}

test('rejects requests while writes are disabled or authentication fails', async () => {
  const dependencies = {
    installationToken: async () => 'github-token',
    publishToken,
  }
  assert.equal((await handleAmpPublishRequest(request(validInput), dependencies)).status, 503)
  assert.equal((await handleAmpPublishRequest(request(validInput, 'wrong'), {
    ...dependencies,
    writesEnabled: true,
  })).status, 401)
})

test('checks the current head and publishes one marked pull request comment', async () => {
  const calls: Array<{ body?: string; method: string; url: string }> = []
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input)
    calls.push({ body: init?.body?.toString(), method: init?.method ?? 'GET', url })
    if (url.endsWith('/pulls/42')) return Response.json({ head: { sha: validInput.headSha } })
    if (url.includes('/comments?')) return Response.json([])
    return Response.json({ html_url: 'https://github.com/phlohouse/phlo/pull/42#issuecomment-1' })
  }

  const response = await handleAmpPublishRequest(request(validInput), {
    fetch: fetcher,
    installationToken: async () => 'github-token',
    publishToken,
    writesEnabled: true,
  })

  assert.equal(response.status, 201)
  assert.deepEqual(calls.map(({ method }) => method), ['GET', 'GET', 'POST'])
  assert.match(calls[2]?.body ?? '', /phlo-agent-delivery:delivery-123/)
})

test('does not publish twice for the same delivery', async () => {
  let writes = 0
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input)
    if (url.endsWith('/pulls/42')) return Response.json({ head: { sha: validInput.headSha } })
    if (url.includes('/comments?')) {
      return Response.json([{
        body: '<!-- phlo-agent-delivery:delivery-123 -->',
        html_url: 'https://github.com/phlohouse/phlo/pull/42#issuecomment-1',
      }])
    }
    if (init?.method === 'POST') writes += 1
    return Response.json({})
  }

  const response = await handleAmpPublishRequest(request(validInput), {
    fetch: fetcher,
    installationToken: async () => 'github-token',
    publishToken,
    writesEnabled: true,
  })

  assert.equal(response.status, 200)
  assert.equal(writes, 0)
})

test('applies approved labels before publishing an issue comment', async () => {
  const calls: Array<{ body?: string; method: string; url: string }> = []
  const fetcher: typeof fetch = async (input, init) => {
    calls.push({
      body: init?.body?.toString(),
      method: init?.method ?? 'GET',
      url: String(input),
    })
    if (String(input).includes('/comments?')) return Response.json([])
    return Response.json({ html_url: 'https://github.com/phlohouse/phlo/issues/42#issuecomment-2' })
  }

  const response = await handleAmpPublishRequest(request({
    ...validInput,
    headSha: undefined,
    kind: 'issue',
    labels: ['bug', 'correctness'],
  }), {
    fetch: fetcher,
    installationToken: async () => 'github-token',
    publishToken,
    writesEnabled: true,
  })

  assert.equal(response.status, 201)
  assert.deepEqual(calls.map(({ method }) => method), ['GET', 'POST', 'POST'])
  assert.match(calls[1]?.url ?? '', /\/labels$/)
  assert.deepEqual(JSON.parse(calls[1]?.body ?? '{}'), { labels: ['bug', 'correctness'] })
})

test('rejects stale pull request reviews and unapproved issue labels', async () => {
  const stale = await handleAmpPublishRequest(request(validInput), {
    fetch: async () => Response.json({ head: { sha: 'b'.repeat(40) } }),
    installationToken: async () => 'github-token',
    publishToken,
    writesEnabled: true,
  })
  assert.equal(stale.status, 409)

  const invalidLabels = await handleAmpPublishRequest(request({
    ...validInput,
    headSha: undefined,
    kind: 'issue',
    labels: ['autorelease: pending'],
  }), {
    installationToken: async () => 'github-token',
    publishToken,
    writesEnabled: true,
  })
  assert.equal(invalidLabels.status, 400)
})
