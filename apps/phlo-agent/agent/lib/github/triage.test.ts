/** Tests for triage label validation: bounded, allowlisted, one label per category. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { issueTriageLabelsAllowed } from './triage.ts'

test('allows a bounded issue triage classification', () => {
  assert.equal(
    issueTriageLabelsAllowed(['bug', 'correctness', 'P1', 'ready-for-agent']),
    true,
  )
})

test('rejects administrative, conflicting, and excessive labels', () => {
  assert.equal(issueTriageLabelsAllowed(['autorelease: pending']), false)
  assert.equal(issueTriageLabelsAllowed(['bug', 'enhancement']), false)
  assert.equal(issueTriageLabelsAllowed(['P1', 'P2']), false)
  assert.equal(issueTriageLabelsAllowed(['security', 'correctness']), false)
  assert.equal(issueTriageLabelsAllowed(['bug', 'correctness', 'P1', 'ready-for-agent', 'audit']), false)
})
