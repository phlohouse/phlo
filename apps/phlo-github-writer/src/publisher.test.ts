/** Contract tests for the writer's bounded GitHub App writes. */
import assert from 'node:assert/strict'
import { createHash, generateKeyPairSync } from 'node:crypto'
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

test('updates only supplied pull request metadata', async () => {
  let patch: { body?: unknown; method?: string; url?: string } = {}
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input)
    const auth = githubAuth(url)
    if (auth !== null) return auth
    patch = {
      body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      method: init?.method ?? 'GET',
      url,
    }
    return Response.json({ html_url: 'https://github.com/phlohouse/phlo/pull/42' })
  }
  const response = await handleRequest(request('/v1/pull-request-metadata', {
    number: 42,
    title: 'fix(agent): reuse review threads',
  }), { ...credentials, fetch: fetcher })

  assert.equal(response.status, 201)
  assert.deepEqual(patch, {
    body: { title: 'fix(agent): reuse review threads' },
    method: 'PATCH',
    url: 'https://api.github.com/repos/phlohouse/phlo/pulls/42',
  })
  assert.equal((await handleRequest(request('/v1/pull-request-metadata', { number: 42 }), credentials)).status, 400)
})

test('resolves a pull request head for an authorized mention', async () => {
  const headSha = 'f'.repeat(40)
  const fetcher: typeof fetch = async (input) => {
    const url = String(input)
    const auth = githubAuth(url)
    if (auth !== null) return auth
    assert.equal(url, 'https://api.github.com/repos/phlohouse/phlo/pulls/42')
    return Response.json({ head: { sha: headSha } })
  }
  const response = await handleRequest(request('/v1/pull-request-head', { number: 42 }), {
    ...credentials,
    fetch: fetcher,
  })

  assert.equal(response.status, 201)
  assert.deepEqual(await response.json(), { headSha })
})

test('creates one draft pull request containing every consumer lock', async () => {
  const baseSha = 'a'.repeat(40)
  const baseTreeSha = 'e'.repeat(40)
  // A root lock and twelve example locks exceed the former 2.5 MB request limit.
  const files = Array.from({ length: 13 }, (_, index) => ({
    path: index === 0 ? 'uv.lock' : `examples/project-${index}/uv.lock`,
    content: `# consumer ${index}\n${'package = []\n'.repeat(70_000)}`,
  }))
  const blobs = new Map<string, string>()
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
    if (url.endsWith('/git/blobs')) {
      const body = JSON.parse(String(init?.body))
      const content = Buffer.from(body.content, 'base64').toString()
      const sha = createHash('sha1').update(content).digest('hex')
      blobs.set(sha, content)
      return Response.json({ sha })
    }
    if (url.endsWith('/git/trees')) return Response.json({ sha: 'c'.repeat(40) })
    if (url.endsWith('/git/commits')) return Response.json({ sha: 'd'.repeat(40) })
    if (url.endsWith('/pulls')) return Response.json({ html_url: 'https://github.com/phlohouse/phlo/pull/99' })
    return Response.json({})
  }
  const response = await handleRequest(request('/v1/draft-pull-requests', {
    baseSha,
    body: 'Verified advisory remediation across all consumers.',
    branch: 'agent/security-dependency-remediation',
    files,
    title: 'fix(deps): remediate advisory in every consumer',
  }), { ...credentials, fetch: fetcher })

  assert.equal(response.status, 201)
  assert.equal((await response.json()).htmlUrl, 'https://github.com/phlohouse/phlo/pull/99')
  const treeRequest = calls.find((call) => call.url.endsWith('/git/trees'))
  assert.deepEqual([...blobs.values()], files.map((file) => file.content))
  assert.deepEqual(treeRequest?.body, {
    base_tree: baseTreeSha,
    tree: files.map((file) => ({
      path: file.path,
      mode: '100644',
      type: 'blob',
      sha: createHash('sha1').update(file.content).digest('hex'),
    })),
  })
  assert.equal((calls.at(-1)?.body as { draft?: unknown }).draft, true)
})

test('bounds large draft payloads without raising limits on other routes', async () => {
  const oversized = JSON.stringify({ content: 'x'.repeat(20_000_000) })
  for (const declared of [false, true]) {
    const draft = new Request('https://writer.example/v1/draft-pull-requests', {
      method: 'POST',
      headers: {
        authorization: `Bearer ${publishToken}`,
        ...(declared ? { 'content-length': String(oversized.length) } : {}),
      },
      body: oversized,
    })
    assert.equal((await handleRequest(draft, credentials)).status, 413)
  }
  assert.equal((await handleRequest(request('/v1/issues', {
    content: 'x'.repeat(2_500_000),
  }), credentials)).status, 413)
})

test('updates an existing agent draft pull request with one new commit', async () => {
  const branch = 'agent/security-dependency-remediation'
  const headSha = 'a'.repeat(40)
  const headTreeSha = 'e'.repeat(40)
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
    if (url.endsWith('/pulls/99')) {
      if (init?.method === 'PATCH') {
        return Response.json({ html_url: 'https://github.com/phlohouse/phlo/pull/99' })
      }
      return Response.json({
        base: { ref: 'main' },
        head: { ref: branch, repo: { full_name: 'phlohouse/phlo' }, sha: headSha },
        state: 'open',
        user: { type: 'Bot' },
      })
    }
    if (url.endsWith(`/git/commits/${headSha}`)) return Response.json({ tree: { sha: headTreeSha } })
    if (url.endsWith('/git/blobs')) return Response.json({ sha: 'b'.repeat(40) })
    if (url.endsWith('/git/trees')) return Response.json({ sha: 'c'.repeat(40) })
    if (url.endsWith('/git/commits')) return Response.json({ sha: 'd'.repeat(40) })
    return Response.json({})
  }
  const response = await handleRequest(request('/v1/draft-pull-requests', {
    body: 'Refreshed advisory evidence.',
    branch,
    files: [{ content: 'fixed lock\n', path: 'uv.lock' }],
    pullNumber: 99,
    title: 'fix(deps): remediate advisories',
  }), { ...credentials, fetch: fetcher })

  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), {
    htmlUrl: 'https://github.com/phlohouse/phlo/pull/99',
    sha: 'd'.repeat(40),
    updated: true,
  })
  const treeRequest = calls.find((call) => call.url.endsWith('/git/trees'))
  assert.equal((treeRequest?.body as { base_tree?: unknown }).base_tree, headTreeSha)
  const commitRequest = calls.find((call) => call.url.endsWith('/git/commits') && call.method === 'POST')
  assert.deepEqual((commitRequest?.body as { parents?: unknown }).parents, [headSha])
  const refRequest = calls.find((call) => call.url.includes('/git/refs/heads/'))
  assert.deepEqual(refRequest?.body, { force: false, sha: 'd'.repeat(40) })
  const metadataRequest = calls.find((call) => call.url.endsWith('/pulls/99') && call.method === 'PATCH')
  assert.deepEqual(metadataRequest?.body, {
    body: 'Refreshed advisory evidence.',
    title: 'fix(deps): remediate advisories',
  })
})

test('refuses to update a pull request it does not own', async () => {
  const fetcher: typeof fetch = async (input) => {
    const auth = githubAuth(String(input))
    if (auth !== null) return auth
    return Response.json({
      base: { ref: 'main' },
      head: { ref: 'agent/human-branch', repo: { full_name: 'phlohouse/phlo' }, sha: 'a'.repeat(40) },
      state: 'open',
      user: { type: 'User' },
    })
  }
  const response = await handleRequest(request('/v1/draft-pull-requests', {
    body: 'Update a human pull request.',
    branch: 'agent/human-branch',
    files: [{ content: 'unsafe\n', path: 'docs/example.md' }],
    pullNumber: 99,
    title: 'docs: rewrite',
  }), { ...credentials, fetch: fetcher })
  assert.equal(response.status, 409)

  const both = await handleRequest(request('/v1/draft-pull-requests', {
    baseSha: 'a'.repeat(40),
    body: 'Ambiguous request.',
    branch: 'agent/fix-docs',
    files: [{ content: 'fixed\n', path: 'docs/example.md' }],
    pullNumber: 99,
    title: 'fix(docs): correct example',
  }), credentials)
  assert.equal(both.status, 400)
})

test('refuses automation changes, unknown labels, and stale maintenance bases', async () => {
  for (const path of ['.github/workflows/ci.yml', '.agents/setup']) {
    const invalid = await handleRequest(request('/v1/draft-pull-requests', {
      baseSha: 'a'.repeat(40),
      body: 'Change automation.',
      branch: 'agent/change-automation',
      files: [{ path, content: 'unsafe\n' }],
      title: 'ci: change automation',
    }), credentials)
    assert.equal(invalid.status, 400)
  }

  const unknownLabel = await handleRequest(request('/v1/issues', {
    body: 'Grounded maintenance finding.',
    labels: ['invented-label'],
    title: 'chore: investigate finding',
  }), credentials)
  assert.equal(unknownLabel.status, 400)

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
