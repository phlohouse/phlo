/** GitHub webhook verification, filtering, and signed review capabilities. */
import { createHmac, timingSafeEqual } from 'node:crypto'

const REPOSITORY = 'phlohouse/phlo'
const CAPABILITY_PREFIX = '[phlo-github-event:v1]'
const DELIVERY_PATTERN = /^[A-Za-z0-9:_-]{1,200}$/
const SHA_PATTERN = /^[0-9a-f]{40}$/i

export interface ReviewTarget {
  deliveryId: string
  headSha?: string
  kind: 'issue' | 'pull_request'
  number: number
  receivedAt: string
}

interface GitHubPayload {
  action?: unknown
  issue?: { number?: unknown }
  number?: unknown
  pull_request?: { draft?: unknown; head?: { sha?: unknown } }
  repository?: { full_name?: unknown }
  sender?: { type?: unknown }
}

function constantTimeEqual(actual: string, expected: string): boolean {
  const actualBytes = Buffer.from(actual)
  const expectedBytes = Buffer.from(expected)
  return actualBytes.length === expectedBytes.length
    && timingSafeEqual(actualBytes, expectedBytes)
}

export function verifyGitHubSignature(body: Uint8Array, signature: string, secret: string): boolean {
  const expected = `sha256=${createHmac('sha256', secret).update(body).digest('hex')}`
  return constantTimeEqual(signature, expected)
}

function validTarget(value: unknown): value is ReviewTarget {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const target = value as Record<string, unknown>
  if (target.kind !== 'issue' && target.kind !== 'pull_request') return false
  if (!Number.isSafeInteger(target.number) || (target.number as number) < 1) return false
  if (typeof target.deliveryId !== 'string' || !DELIVERY_PATTERN.test(target.deliveryId)) return false
  if (typeof target.receivedAt !== 'string' || !Number.isFinite(Date.parse(target.receivedAt))) return false
  if (target.kind === 'pull_request') {
    return typeof target.headSha === 'string' && SHA_PATTERN.test(target.headSha)
  }
  return target.headSha === undefined
}

function targetJson(target: ReviewTarget): string {
  return JSON.stringify({
    deliveryId: target.deliveryId,
    ...(target.headSha === undefined ? {} : { headSha: target.headSha }),
    kind: target.kind,
    number: target.number,
    receivedAt: target.receivedAt,
  })
}

export function createCapability(target: ReviewTarget, secret: string): string {
  const encoded = Buffer.from(targetJson(target)).toString('base64url')
  const signature = createHmac('sha256', secret).update(encoded).digest('base64url')
  return `${CAPABILITY_PREFIX} ${encoded}.${signature}`
}

export function parseCapability(message: string, secret: string): ReviewTarget | null {
  const line = message.split('\n', 1)[0]
  if (!line?.startsWith(`${CAPABILITY_PREFIX} `)) return null
  const token = line.slice(CAPABILITY_PREFIX.length + 1)
  const separator = token.lastIndexOf('.')
  if (separator < 1) return null
  const encoded = token.slice(0, separator)
  const signature = token.slice(separator + 1)
  const expected = createHmac('sha256', secret).update(encoded).digest('base64url')
  if (!constantTimeEqual(signature, expected)) return null

  try {
    const target = JSON.parse(Buffer.from(encoded, 'base64url').toString()) as unknown
    return validTarget(target) ? target : null
  } catch {
    return null
  }
}

export function parseGitHubEvent(
  body: Uint8Array,
  headers: Readonly<Record<string, string>>,
  receivedAt: string,
): ReviewTarget | null {
  const deliveryId = headers['x-github-delivery']
  if (deliveryId === undefined || !DELIVERY_PATTERN.test(deliveryId)) return null

  let payload: GitHubPayload
  try {
    payload = JSON.parse(Buffer.from(body).toString()) as GitHubPayload
  } catch {
    return null
  }
  if (
    payload.repository?.full_name?.toString().toLowerCase() !== REPOSITORY
    || payload.sender?.type === 'Bot'
  ) {
    return null
  }

  const number = payload.number ?? payload.issue?.number
  if (!Number.isSafeInteger(number) || (number as number) < 1) return null
  if (headers['x-github-event'] === 'issues' && payload.action === 'opened') {
    return { deliveryId, kind: 'issue', number: number as number, receivedAt }
  }
  if (headers['x-github-event'] !== 'pull_request') return null
  const shouldReview = payload.action === 'ready_for_review'
    || (payload.action === 'opened' && payload.pull_request?.draft !== true)
  const headSha = payload.pull_request?.head?.sha
  if (!shouldReview || typeof headSha !== 'string' || !SHA_PATTERN.test(headSha)) return null
  return { deliveryId, headSha, kind: 'pull_request', number: number as number, receivedAt }
}

export const capabilityPrefix = CAPABILITY_PREFIX
