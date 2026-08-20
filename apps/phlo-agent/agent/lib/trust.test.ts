import assert from 'node:assert/strict'
import test from 'node:test'
import { autonomousWritesEnabled } from './trust.ts'

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
