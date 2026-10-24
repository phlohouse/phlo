/** Tests production caching at the HTTP boundary after `npm run build`. */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { readFile, readdir } from 'node:fs/promises'
import { createServer } from 'node:net'
import { after, before, test } from 'node:test'
import { setTimeout } from 'node:timers/promises'

let server
let origin

before(async () => {
  const portFinder = createServer()
  await new Promise((resolve, reject) => {
    portFinder.once('error', reject)
    portFinder.listen(0, '127.0.0.1', resolve)
  })
  const { port } = portFinder.address()
  await new Promise((resolve) => portFinder.close(resolve))
  origin = `http://127.0.0.1:${port}`
  server = spawn(process.execPath, ['.output/server/index.mjs'], {
    env: { ...process.env, HOST: '127.0.0.1', PORT: String(port) },
    stdio: 'ignore',
  })
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const ready = await fetch(`${origin}/healthz`, {
        signal: AbortSignal.timeout(1000),
      })
      await ready.body?.cancel()
      return
    } catch {
      if (server.exitCode !== null) throw new Error('Production server exited')
      await setTimeout(100)
    }
  }
  throw new Error('Production server did not become ready')
})

after(() => server?.kill('SIGKILL'))

for (const path of [
  '/?env=sandbox',
  '/assets/silver%2Fqc_results?env=sandbox&tab=audits',
  '/_serverFn/missing-function',
]) {
  test(`dynamic response ${path} cannot be stored in a browser or shared cache`, async () => {
    const response = await fetch(`${origin}${path}`, {
      signal: AbortSignal.timeout(5000),
    })
    assert.equal(response.headers.get('cache-control'), 'private, no-store')
    await response.body?.cancel()
  })
}

test('hashed JavaScript retains immutable caching in a separate URL namespace', async () => {
  const files = await readdir('.output/public/_build')
  const script = files.find((name) => name.endsWith('.js'))
  assert.ok(script, 'Expected a compiled JavaScript asset under /_build')
  const response = await fetch(`${origin}/_build/${script}`, {
    signal: AbortSignal.timeout(5000),
  })
  assert.equal(response.status, 200)
  assert.equal(
    response.headers.get('cache-control'),
    'public, max-age=31536000, immutable',
  )
  assert.match(response.headers.get('content-type'), /javascript/)
  await response.body?.cancel()
})

test('oversized server-function bodies are rejected with private no-store caching', async () => {
  const response = await fetch(`${origin}/_serverFn/missing-function`, {
    method: 'POST',
    signal: AbortSignal.timeout(5000),
    headers: { 'content-type': 'application/octet-stream' },
    body: new Uint8Array(12 * 1024 * 1024 + 1),
  })
  assert.equal(response.status, 413)
  assert.equal(response.headers.get('cache-control'), 'private, no-store')
  await response.body?.cancel()
})

test('normal multipart and JSON POSTs reach validation through the production framework', async () => {
  const chunks = await readdir('.output/server/_ssr')
  const uploadChunk = chunks.find((name) => /^uploads-.*\.mjs$/.test(name))
  assert.ok(uploadChunk, 'Expected the compiled CSV server-function module')
  const source = await readFile(`.output/server/_ssr/${uploadChunk}`, 'utf8')
  const id = source.match(/id: "([a-f0-9]+)",\s*name: "retainCsvUpload"/)?.[1]
  assert.ok(id, 'Expected the real retainCsvUpload server-function ID')
  const form = new FormData()
  form.set('env', 'sandbox')
  // Missing file exercises real input validation without retaining or loading data.
  const response = await fetch(`${origin}/_serverFn/${id}`, {
    method: 'POST',
    signal: AbortSignal.timeout(5000),
    headers: { 'x-tsr-serverFn': 'true', origin },
    body: form,
  })
  assert.equal(response.headers.get('cache-control'), 'private, no-store')
  const body = await response.text()
  assert.match(body, /Choose a CSV file/)
  assert.doesNotMatch(
    body,
    /private member|disturbed or locked|Internal Server Error/,
  )
  // JSON calls use the same browser POST path as Save Transform. A valid
  // serialized empty payload must reach this function's FormData validator.
  const jsonResponse = await fetch(`${origin}/_serverFn/${id}`, {
    method: 'POST',
    signal: AbortSignal.timeout(5000),
    headers: {
      'x-tsr-serverFn': 'true',
      'content-type': 'application/json',
      origin,
    },
    body: JSON.stringify({
      t: { t: 10, i: 0, p: { k: [], v: [] }, o: 0 },
      f: 127,
      m: [],
    }),
  })
  assert.equal(jsonResponse.headers.get('cache-control'), 'private, no-store')
  assert.match(await jsonResponse.text(), /Choose a CSV file/)
})
