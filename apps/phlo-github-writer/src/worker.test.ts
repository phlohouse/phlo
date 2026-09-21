import assert from 'node:assert/strict'
import test from 'node:test'
import worker from './worker.js'

test('the Cloudflare Worker exposes its health check without credentials', async () => {
  const response = await worker.fetch(new Request('https://writer.example/health'), {})

  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), { ok: true })
})
