/** Tests for GitHub event filtering, signatures, and review capabilities. */
import assert from 'node:assert/strict'
import { createHmac } from 'node:crypto'
import test from 'node:test'
import {
  createCapability,
  parseCapability,
  parseGitHubEvent,
  parseGitHubMention,
  reviewParentThreadID,
  verifyGitHubSignature,
  type ReviewTarget,
} from './lib.ts'

const secret = 'test-secret'

test('verifies GitHub signatures over the exact body bytes', () => {
  const body = Buffer.from('{"action":"opened"}')
  const signature = `sha256=${createHmac('sha256', secret).update(body).digest('hex')}`
  assert.equal(verifyGitHubSignature(body, signature, secret), true)
  assert.equal(verifyGitHubSignature(Buffer.from('{}'), signature, secret), false)
})

test('round-trips signed review capabilities and rejects tampering', () => {
  const target: ReviewTarget = {
    deliveryId: 'delivery-123',
    headSha: 'a'.repeat(40),
    kind: 'pull_request',
    number: 42,
    receivedAt: '2026-09-20T10:00:00.000Z',
  }
  const capability = createCapability(target, secret)
  assert.deepEqual(parseCapability(`Review Phlo PR #42 @ ${target.headSha}\n${capability}\nReview it.`, secret), target)
  const replacement = capability.endsWith('a') ? 'b' : 'a'
  assert.equal(parseCapability(`${capability.slice(0, -1)}${replacement}`, secret), null)
})

test('accepts only matching Phlo issue and pull request triggers', () => {
  const receivedAt = '2026-09-20T10:00:00.000Z'
  const base = {
    repository: { full_name: 'phlohouse/phlo' },
    sender: { type: 'User' },
  }
  const issueBody = Buffer.from(JSON.stringify({ ...base, action: 'opened', issue: { number: 17 } }))
  assert.deepEqual(parseGitHubEvent(issueBody, {
    'x-github-delivery': 'issue-event',
    'x-github-event': 'issues',
  }, receivedAt), {
    deliveryId: 'issue-event',
    kind: 'issue',
    number: 17,
    receivedAt,
  })

  const pullBody = Buffer.from(JSON.stringify({
    ...base,
    action: 'ready_for_review',
    number: 18,
    pull_request: { draft: false, head: { sha: 'b'.repeat(40) } },
  }))
  assert.deepEqual(parseGitHubEvent(pullBody, {
    'x-github-delivery': 'pull-event',
    'x-github-event': 'pull_request',
  }, receivedAt), {
    deliveryId: 'pull-event',
    headSha: 'b'.repeat(40),
    kind: 'pull_request',
    number: 18,
    receivedAt,
  })

  const botBody = Buffer.from(JSON.stringify({ ...base, sender: { type: 'Bot' }, action: 'opened', issue: { number: 19 } }))
  assert.equal(parseGitHubEvent(botBody, {
    'x-github-delivery': 'bot-event',
    'x-github-event': 'issues',
  }, receivedAt), null)
})

test('accepts phlo-agent mentions only from trusted collaborators', () => {
  const receivedAt = '2026-09-20T10:00:00.000Z'
  const payload = {
    action: 'created',
    comment: {
      author_association: 'OWNER',
      body: '@phlo-agent rewrite the PR description',
    },
    issue: { number: 42, pull_request: { url: 'https://api.github.com/repos/phlohouse/phlo/pulls/42' } },
    repository: { full_name: 'phlohouse/phlo' },
    sender: { login: 'iamgp', type: 'User' },
  }
  assert.deepEqual(parseGitHubMention(Buffer.from(JSON.stringify(payload)), {
    'x-github-delivery': 'mention-event',
    'x-github-event': 'issue_comment',
  }, receivedAt), {
    author: 'iamgp',
    deliveryId: 'mention-event',
    kind: 'pull_request',
    number: 42,
    receivedAt,
    request: 'rewrite the PR description',
  })

  assert.equal(parseGitHubMention(Buffer.from(JSON.stringify({
    ...payload,
    comment: { ...payload.comment, author_association: 'NONE' },
  })), {
    'x-github-delivery': 'untrusted-mention',
    'x-github-event': 'issue_comment',
  }, receivedAt), null)
  assert.equal(parseGitHubMention(Buffer.from(JSON.stringify({
    ...payload,
    comment: { ...payload.comment, body: 'No mention here.' },
  })), {
    'x-github-delivery': 'no-mention',
    'x-github-event': 'issue_comment',
  }, receivedAt), null)
})

test('uses the configured automation host for review threads', () => {
  const webhookThread = 'T-webhook-owner'

  assert.equal(reviewParentThreadID('T-automation-host', webhookThread), 'T-automation-host')
  assert.equal(reviewParentThreadID(undefined, webhookThread), webhookThread)
  assert.equal(reviewParentThreadID('automation-host', webhookThread), webhookThread)
})
