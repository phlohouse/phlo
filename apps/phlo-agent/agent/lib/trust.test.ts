/** Tests for trust checks: the autonomous-writes flag and issue-triage auth scoping. */
import assert from 'node:assert/strict'
import test from 'node:test'
import type { SessionAuthContext } from 'eve/context'
import { autonomousWritesEnabled, isGitHubIssueTriageAuth } from './trust.ts'

test('autonomous writes are disabled unless explicitly enabled', () => {
  const previous = process.env.PHLO_AGENT_AUTONOMOUS_WRITES
  try {
    delete process.env.PHLO_AGENT_AUTONOMOUS_WRITES
    assert.equal(autonomousWritesEnabled(), false)
    process.env.PHLO_AGENT_AUTONOMOUS_WRITES = '0'
    assert.equal(autonomousWritesEnabled(), false)
    process.env.PHLO_AGENT_AUTONOMOUS_WRITES = 'true'
    assert.equal(autonomousWritesEnabled(), false)
    process.env.PHLO_AGENT_AUTONOMOUS_WRITES = '1'
    assert.equal(autonomousWritesEnabled(), true)
  } finally {
    if (previous === undefined) delete process.env.PHLO_AGENT_AUTONOMOUS_WRITES
    else process.env.PHLO_AGENT_AUTONOMOUS_WRITES = previous
  }
})

const issueAuth: SessionAuthContext = {
  attributes: {
    conversation_kind: 'issue',
    issue_number: '717',
    repository: 'phlohouse/phlo',
  },
  authenticator: 'github-webhook',
  issuer: 'github:phlohouse',
  principalId: 'github:123',
  principalType: 'user',
  subject: 'maintainer',
}

test('issue triage auth is scoped to the triggering Phlo issue', () => {
  assert.equal(isGitHubIssueTriageAuth(issueAuth, 717), true)
  assert.equal(isGitHubIssueTriageAuth(issueAuth, 718), false)
  assert.equal(
    isGitHubIssueTriageAuth({
      ...issueAuth,
      attributes: { ...issueAuth.attributes, repository: 'other/repo' },
    }, 717),
    false,
  )
  assert.equal(isGitHubIssueTriageAuth(null, 717), false)
})
