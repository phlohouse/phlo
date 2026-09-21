import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import test from 'node:test'
import { handleRequest } from './publisher.js'

const publishToken = 'writer-secret-that-is-at-least-32-bytes'
const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
const credentials = {
  appId: '4662586',
  privateKey: privateKey.export({ format: 'pem', type: 'pkcs8' }).toString(),
  publishToken,
}

function request(path: string, body: unknown, token = publishToken): Request {
  return new Request(`https://writer.example${path}`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function githubAuth(url: string): Response | null {
  if (url.endsWith('/repos/phlohouse/phlo/installation')) return Response.json({ id: 123 })
  if (url.endsWith('/app/installations/123/access_tokens')) return Response.json({ token: 'installation-token' })
  return null
}

test('health is public and writes require the bridge token', async () => {
  assert.equal((await handleRequest(new Request('https://writer.example/health'), {})).status, 200)
  assert.equal((await handleRequest(request('/v1/issues', {}, 'wrong'), credentials)).status, 401)
})

test('checks the pull request head and deduplicates review comments', async () => {
  const deliveryId = 'delivery-123'
  const headSha = 'a'.repeat(40)
  let commentPages = 0
  let writes = 0
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input)
    const auth = githubAuth(url)
    if (auth !== null) return auth
    if (url.endsWith('/pulls/42')) return Response.json({ head: { sha: headSha } })
    if (url.includes('/comments?')) {
      commentPages += 1
      if (commentPages === 1) {
        return Response.json([], {
          headers: {
            link: '<https://api.github.com/repos/phlohouse/phlo/issues/42/comments?per_page=100&page=2>; rel="next"',
          },
        })
      }
      return Response.json([
        {
          body: `<!-- phlo-agent-delivery:${deliveryId} -->`,
          html_url: 'https://github.com/phlohouse/phlo/pull/42#issuecomment-1',
        },
      ])
    }
    if (init?.method === 'POST') writes += 1
    return Response.json({})
  }
  const response = await handleRequest(request('/v1/github-comments', {
    body: 'No actionable findings.',
    deliveryId,
    headSha,
    kind: 'pull_request',
    labels: [],
    number: 42,
    receivedAt: '2026-09-20T10:00:00.000Z',
  }), { ...credentials, fetch: fetcher })

  assert.equal(response.status, 200)
  assert.equal(commentPages, 2)
  assert.equal(writes, 0)
})

test('creates a draft pull request from bounded file content', async () => {
  const baseSha = 'a'.repeat(40)
  const baseTreeSha = 'e'.repeat(40)
  const calls: Array<{ body: unknown; method: string; url: string }> = []
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input)
    const auth = githubAuth(url)
    if (auth !== null) return auth
    calls.push({
      body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      method: init?.method ?? 'GET',
      url,
    })
    if (url.endsWith('/git/ref/heads/main')) return Response.json({ object: { sha: baseSha } })
    if (url.endsWith(`/git/commits/${baseSha}`)) return Response.json({ tree: { sha: baseTreeSha } })
    if (url.endsWith('/git/blobs')) return Response.json({ sha: 'b'.repeat(40) })
    if (url.endsWith('/git/trees')) return Response.json({ sha: 'c'.repeat(40) })
    if (url.endsWith('/git/commits')) return Response.json({ sha: 'd'.repeat(40) })
    if (url.endsWith('/pulls')) return Response.json({ html_url: 'https://github.com/phlohouse/phlo/pull/99' })
    return Response.json({})
  }
  const response = await handleRequest(request('/v1/draft-pull-requests', {
    baseSha,
    body: 'Verified maintenance fix.',
    branch: 'agent/fix-docs-2026-09-21',
    files: [{ path: 'docs/example.md', content: 'fixed\n' }],
    title: 'fix(docs): correct example',
  }), { ...credentials, fetch: fetcher })

  assert.equal(response.status, 201)
  assert.deepEqual(calls.map((call) => call.method), ['GET', 'GET', 'POST', 'POST', 'POST', 'POST', 'POST'])
  const treeRequest = calls.find((call) => call.url.endsWith('/git/trees'))
  assert.equal((treeRequest?.body as { base_tree?: unknown }).base_tree, baseTreeSha)
  assert.equal((calls.at(-1)?.body as { draft?: unknown }).draft, true)
})

test('refuses workflow changes and stale maintenance bases', async () => {
  const invalid = await handleRequest(request('/v1/draft-pull-requests', {
    baseSha: 'a'.repeat(40),
    body: 'Change CI.',
    branch: 'agent/change-ci',
    files: [{ path: '.github/workflows/ci.yml', content: 'unsafe\n' }],
    title: 'ci: change workflow',
  }), credentials)
  assert.equal(invalid.status, 400)

  const fetcher: typeof fetch = async (input) => {
    const auth = githubAuth(String(input))
    if (auth !== null) return auth
    return Response.json({ object: { sha: 'b'.repeat(40) } })
  }
  const stale = await handleRequest(request('/v1/draft-pull-requests', {
    baseSha: 'a'.repeat(40),
    body: 'Fix docs.',
    branch: 'agent/fix-docs',
    files: [{ path: 'docs/example.md', content: 'fixed\n' }],
    title: 'fix(docs): correct example',
  }), { ...credentials, fetch: fetcher })
  assert.equal(stale.status, 409)
})
