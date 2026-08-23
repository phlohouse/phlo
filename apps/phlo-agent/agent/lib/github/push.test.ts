/** Tests for push branch validation: feature branches pass, protected or unsafe refs are refused. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { validatePushBranch } from './push.ts'

test('accepts feature branches', () => {
  assert.equal(validatePushBranch('agent/fix-docs-2026-08-20'), null)
})

test('rejects protected and unsafe refs', () => {
  for (const branch of [
    'main',
    'master',
    'HEAD',
    'refs/heads/feature',
    '../main',
    'feature//nested',
    'feature;curl-example.com',
    'feature $(command)',
  ]) {
    assert.notEqual(validatePushBranch(branch), null, branch)
  }
})
