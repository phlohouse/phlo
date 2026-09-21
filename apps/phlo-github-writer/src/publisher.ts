import { timingSafeEqual } from 'node:crypto'
import { githubRequest, installationToken, repository, type Fetcher } from './github.js'
import { issueTriageLabelsAllowed, validBranch, validChangedPath } from './validation.js'

export const maxRequestLength = 2_500_000
const MAX_BODY_LENGTH = 60_000
const DELIVERY_PATTERN = /^[A-Za-z0-9:_-]{1,200}$/
const SHA_PATTERN = /^[0-9a-f]{40}$/i
const MARKER_PREFIX = '<!-- phlo-agent-delivery:'

interface Dependencies {
  appId?: string
  fetch?: Fetcher
  privateKey?: string
  publishToken?: string
}

interface ReviewRequest {
  body: string
  deliveryId: string
  headSha?: string
  kind: 'issue' | 'pull_request'
  labels: string[]
  number: number
  receivedAt: string
}

interface ChangedFile {
  content: string | null
  path: string
}

function safeEqual(actual: string, expected: string): boolean {
  const left = Buffer.from(actual)
  const right = Buffer.from(expected)
  return left.length === right.length && timingSafeEqual(left, right)
}

function authorized(request: Request, expected: string | undefined): boolean {
  if (expected === undefined || expected.length < 32) return false
  const match = request.headers.get('authorization')?.match(/^Bearer ([^\s]+)$/i)
  return match?.[1] !== undefined && safeEqual(match[1], expected)
}

async function input(request: Request): Promise<unknown> {
  const contentLength = Number(request.headers.get('content-length'))
  if (Number.isFinite(contentLength) && contentLength > maxRequestLength) {
    throw new RangeError('Request body is too large.')
  }
  const raw = await request.text()
  if (raw.length > maxRequestLength) throw new RangeError('Request body is too large.')
  return JSON.parse(raw) as unknown
}

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function nextPage(link: string | null): string | null {
  if (link === null) return null
  for (const part of link.split(',')) {
    const match = part.match(/<([^>]+)>;\s*rel="next"/)
    if (match?.[1] === undefined) continue
    const url = new URL(match[1])
    if (url.origin !== 'https://api.github.com') {
      throw new Error('GitHub returned an invalid pagination URL.')
    }
    return `${url.pathname}${url.search}`
  }
  return null
}

function reviewInput(value: unknown): ReviewRequest | null {
  const data = object(value)
  if (data === null || (data.kind !== 'issue' && data.kind !== 'pull_request')) return null
  if (!Number.isSafeInteger(data.number) || (data.number as number) < 1) return null
  if (typeof data.body !== 'string' || data.body.length === 0 || data.body.length > MAX_BODY_LENGTH) return null
  if (data.body.includes(MARKER_PREFIX)) return null
  if (typeof data.deliveryId !== 'string' || !DELIVERY_PATTERN.test(data.deliveryId)) return null
  if (typeof data.receivedAt !== 'string' || !Number.isFinite(Date.parse(data.receivedAt))) return null
  const labels = data.labels ?? []
  if (!issueTriageLabelsAllowed(labels)) return null
  if (data.kind === 'pull_request') {
    if (typeof data.headSha !== 'string' || !SHA_PATTERN.test(data.headSha) || labels.length > 0) return null
  } else if (data.headSha !== undefined) return null
  return {
    body: data.body,
    deliveryId: data.deliveryId,
    ...(data.kind === 'pull_request' ? { headSha: data.headSha as string } : {}),
    kind: data.kind,
    labels,
    number: data.number as number,
    receivedAt: data.receivedAt,
  }
}

async function existingComment(
  review: ReviewRequest,
  token: string,
  fetcher: Fetcher,
): Promise<{ body?: unknown; html_url?: unknown } | null> {
  const marker = `${MARKER_PREFIX}${review.deliveryId} -->`
  const since = encodeURIComponent(review.receivedAt)
  let path: string | null = `/repos/${repository}/issues/${review.number}/comments?per_page=100&since=${since}`
  while (path !== null) {
    const response = await githubRequest(path, token, fetcher)
    if (!response.ok) throw new Error(`GitHub comment lookup failed with HTTP ${response.status}.`)
    const comments = await response.json() as Array<{ body?: unknown; html_url?: unknown }>
    const found = comments.find((comment) =>
      typeof comment.body === 'string' && comment.body.includes(marker)
    )
    if (found !== undefined) return found
    path = nextPage(response.headers.get('link'))
  }
  return null
}

async function publishReview(review: ReviewRequest, token: string, fetcher: Fetcher) {
  if (review.kind === 'pull_request') {
    const response = await githubRequest(`/repos/${repository}/pulls/${review.number}`, token, fetcher)
    if (!response.ok) throw new Error(`GitHub pull request lookup failed with HTTP ${response.status}.`)
    const pull = await response.json() as { head?: { sha?: unknown } }
    if (pull.head?.sha !== review.headSha) throw new Error('The pull request head changed after the review started.')
  }
  const duplicate = await existingComment(review, token, fetcher)
  if (duplicate !== null) {
    return { htmlUrl: typeof duplicate.html_url === 'string' ? duplicate.html_url : '', reused: true }
  }
  if (review.kind === 'issue' && review.labels.length > 0) {
    const response = await githubRequest(`/repos/${repository}/issues/${review.number}/labels`, token, fetcher, {
      method: 'POST',
      body: JSON.stringify({ labels: review.labels }),
    })
    if (!response.ok) throw new Error(`GitHub label update failed with HTTP ${response.status}.`)
  }
  const marker = `${MARKER_PREFIX}${review.deliveryId} -->`
  const response = await githubRequest(`/repos/${repository}/issues/${review.number}/comments`, token, fetcher, {
    method: 'POST',
    body: JSON.stringify({ body: `${review.body}\n\n${marker}` }),
  })
  if (!response.ok) throw new Error(`GitHub comment creation failed with HTTP ${response.status}.`)
  const comment = await response.json() as { html_url?: unknown }
  return { htmlUrl: typeof comment.html_url === 'string' ? comment.html_url : '', reused: false }
}

function issueInput(value: unknown): { body: string; labels: string[]; title: string } | null {
  const data = object(value)
  const labels = data?.labels ?? []
  if (data === null || typeof data.title !== 'string' || data.title.length < 1 || data.title.length > 256) return null
  if (typeof data.body !== 'string' || data.body.length < 1 || data.body.length > MAX_BODY_LENGTH) return null
  if (!issueTriageLabelsAllowed(labels)) return null
  return { body: data.body, labels, title: data.title }
}

function pullRequestInput(value: unknown): {
  baseSha: string
  body: string
  branch: string
  files: ChangedFile[]
  title: string
} | null {
  const data = object(value)
  if (data === null || typeof data.baseSha !== 'string' || !SHA_PATTERN.test(data.baseSha)) return null
  if (!validBranch(data.branch) || typeof data.title !== 'string' || data.title.length < 1 || data.title.length > 256) return null
  if (typeof data.body !== 'string' || data.body.length < 1 || data.body.length > MAX_BODY_LENGTH) return null
  if (!Array.isArray(data.files) || data.files.length < 1 || data.files.length > 50) return null
  const files: ChangedFile[] = []
  const paths = new Set<string>()
  for (const candidate of data.files) {
    const file = object(candidate)
    if (file === null || !validChangedPath(file.path) || (typeof file.content !== 'string' && file.content !== null)) return null
    if (paths.has(file.path)) return null
    paths.add(file.path)
    files.push({ content: file.content, path: file.path })
  }
  return { baseSha: data.baseSha, body: data.body, branch: data.branch, files, title: data.title }
}

async function publishIssue(issue: ReturnType<typeof issueInput> & {}, token: string, fetcher: Fetcher) {
  const response = await githubRequest(`/repos/${repository}/issues`, token, fetcher, {
    method: 'POST',
    body: JSON.stringify(issue),
  })
  if (!response.ok) throw new Error(`GitHub issue creation failed with HTTP ${response.status}.`)
  const result = await response.json() as { html_url?: unknown }
  return { htmlUrl: typeof result.html_url === 'string' ? result.html_url : '' }
}

async function publishPullRequest(
  pull: ReturnType<typeof pullRequestInput> & {},
  token: string,
  fetcher: Fetcher,
) {
  const reference = await githubRequest(`/repos/${repository}/git/ref/heads/main`, token, fetcher)
  if (!reference.ok) throw new Error(`GitHub main reference lookup failed with HTTP ${reference.status}.`)
  const current = await reference.json() as { object?: { sha?: unknown } }
  if (current.object?.sha !== pull.baseSha) throw new Error('The main branch changed after maintenance started.')

  const baseCommitResponse = await githubRequest(
    `/repos/${repository}/git/commits/${pull.baseSha}`,
    token,
    fetcher,
  )
  if (!baseCommitResponse.ok) {
    throw new Error(`GitHub base commit lookup failed with HTTP ${baseCommitResponse.status}.`)
  }
  const baseCommit = await baseCommitResponse.json() as { tree?: { sha?: unknown } }
  if (typeof baseCommit.tree?.sha !== 'string') {
    throw new Error('GitHub returned an invalid base tree SHA.')
  }

  const tree = []
  for (const file of pull.files) {
    if (file.content === null) {
      tree.push({ path: file.path, mode: '100644', type: 'blob', sha: null })
      continue
    }
    const blobResponse = await githubRequest(`/repos/${repository}/git/blobs`, token, fetcher, {
      method: 'POST',
      body: JSON.stringify({ content: Buffer.from(file.content).toString('base64'), encoding: 'base64' }),
    })
    if (!blobResponse.ok) throw new Error(`GitHub blob creation failed with HTTP ${blobResponse.status}.`)
    const blob = await blobResponse.json() as { sha?: unknown }
    if (typeof blob.sha !== 'string') throw new Error('GitHub returned an invalid blob SHA.')
    tree.push({ path: file.path, mode: '100644', type: 'blob', sha: blob.sha })
  }
  const treeResponse = await githubRequest(`/repos/${repository}/git/trees`, token, fetcher, {
    method: 'POST',
    body: JSON.stringify({ base_tree: baseCommit.tree.sha, tree }),
  })
  if (!treeResponse.ok) throw new Error(`GitHub tree creation failed with HTTP ${treeResponse.status}.`)
  const treeResult = await treeResponse.json() as { sha?: unknown }
  if (typeof treeResult.sha !== 'string') throw new Error('GitHub returned an invalid tree SHA.')

  const commitResponse = await githubRequest(`/repos/${repository}/git/commits`, token, fetcher, {
    method: 'POST',
    body: JSON.stringify({ message: pull.title, tree: treeResult.sha, parents: [pull.baseSha] }),
  })
  if (!commitResponse.ok) throw new Error(`GitHub commit creation failed with HTTP ${commitResponse.status}.`)
  const commit = await commitResponse.json() as { sha?: unknown }
  if (typeof commit.sha !== 'string') throw new Error('GitHub returned an invalid commit SHA.')

  const branchResponse = await githubRequest(`/repos/${repository}/git/refs`, token, fetcher, {
    method: 'POST',
    body: JSON.stringify({ ref: `refs/heads/${pull.branch}`, sha: commit.sha }),
  })
  if (!branchResponse.ok) throw new Error(`GitHub branch creation failed with HTTP ${branchResponse.status}.`)
  const pullResponse = await githubRequest(`/repos/${repository}/pulls`, token, fetcher, {
    method: 'POST',
    body: JSON.stringify({
      base: 'main',
      body: pull.body,
      draft: true,
      head: pull.branch,
      title: pull.title,
    }),
  })
  if (!pullResponse.ok) throw new Error(`GitHub pull request creation failed with HTTP ${pullResponse.status}.`)
  const result = await pullResponse.json() as { html_url?: unknown }
  return { htmlUrl: typeof result.html_url === 'string' ? result.html_url : '', sha: commit.sha }
}

export async function handleRequest(request: Request, dependencies: Dependencies): Promise<Response> {
  const url = new URL(request.url)
  if (request.method === 'GET' && url.pathname === '/health') return Response.json({ ok: true })
  if (request.method !== 'POST' || !authorized(request, dependencies.publishToken)) {
    return Response.json({ error: 'Unauthorized.' }, { status: 401 })
  }

  let value: unknown
  try {
    value = await input(request)
  } catch (error) {
    if (error instanceof RangeError) {
      return Response.json({ error: error.message }, { status: 413 })
    }
    return Response.json({ error: 'Invalid JSON.' }, { status: 400 })
  }
  const parsed = url.pathname === '/v1/github-comments'
    ? reviewInput(value)
    : url.pathname === '/v1/issues'
      ? issueInput(value)
      : url.pathname === '/v1/draft-pull-requests'
        ? pullRequestInput(value)
        : null
  if (parsed === null) return Response.json({ error: 'Invalid request.' }, { status: 400 })
  if (dependencies.appId === undefined || dependencies.privateKey === undefined) {
    return Response.json({ error: 'GitHub App credentials are not configured.' }, { status: 503 })
  }

  try {
    const fetcher = dependencies.fetch ?? fetch
    const token = await installationToken(dependencies.appId, dependencies.privateKey, fetcher)
    const result = url.pathname === '/v1/github-comments'
      ? await publishReview(parsed as ReviewRequest, token, fetcher)
      : url.pathname === '/v1/issues'
        ? await publishIssue(parsed as ReturnType<typeof issueInput> & {}, token, fetcher)
        : await publishPullRequest(parsed as ReturnType<typeof pullRequestInput> & {}, token, fetcher)
    return Response.json(result, { status: 'reused' in result && result.reused ? 200 : 201 })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'GitHub publishing failed.'
    const status = message.includes('changed after') ? 409 : 502
    return Response.json({ error: message }, { status })
  }
}
