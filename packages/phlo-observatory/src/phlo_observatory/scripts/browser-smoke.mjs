/** Repeatable authenticated smoke against the production Node bundle and HTTP fixture. */
import assert from 'node:assert/strict'
import { execFile, spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { promisify } from 'node:util'
import { startApiFixture } from './api-fixture.mjs'

const session = `smoke-${process.pid}`
const artifactDir = resolve(
  process.env.SMOKE_ARTIFACT_DIR ?? '../../../../.amp/in/artifacts/998-browser',
)
mkdirSync(artifactDir, { recursive: true })
const fixture = await startApiFixture()
const port = process.env.SMOKE_PORT ?? '3109'
const base = `http://127.0.0.1:${port}`
const app = spawn(process.execPath, ['.output/server/index.mjs'], {
  env: {
    ...process.env,
    PORT: port,
    HOST: '127.0.0.1',
    PHLO_API_URL: fixture.url,
  },
  stdio: ['ignore', 'pipe', 'pipe'],
})
let logs = ''
app.stdout.on('data', (data) => {
  logs += data
})
app.stderr.on('data', (data) => {
  logs += data
})

async function browser(...args) {
  const { stdout } = await promisify(execFile)(
    'agent-browser',
    ['--session', session, ...args, '--json'],
    {
      encoding: 'utf8',
      timeout: 40_000,
    },
  )
  const result = JSON.parse(stdout)
  assert(result.success, result.error)
  return result.data
}
async function capture(name) {
  await browser('screenshot', resolve(artifactDir, `${name}.png`))
}

try {
  let ready = false
  for (let attempt = 0; attempt < 100; attempt++) {
    if (app.exitCode !== null) throw new Error(`Built app exited: ${logs}`)
    try {
      await fetch(base)
      ready = true
      break
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100))
    }
  }
  assert(ready, 'Built app did not listen')
  await browser(
    '--headers',
    JSON.stringify({ Authorization: 'Bearer browser-smoke-user' }),
    'open',
    `${base}/?env=staging`,
  )
  await browser('set', 'viewport', '1280', '800', '2')
  await browser('wait', '--text', '0 of 7 fresh')
  assert.equal((await browser('get', 'text', 'h1')).text, 'Overview')
  assert.match((await browser('get', 'text', 'main')).text, /0 of 7 fresh/)
  await capture('01-startup')
  // Navigate through the real application link, not a direct route load.
  await browser(
    'click',
    'nav[aria-label="Primary navigation"] a[href="/query?env=staging"]',
  )
  await browser('wait', '.cm-content')
  assert.match((await browser('get', 'url')).url, /\/query\?env=staging/)
  await browser('click', '.cm-content')
  await browser('press', 'Control+A')
  await browser('press', 'Backspace')
  await browser('type', '.cm-content', 'SELECT 7 AS answer')
  await browser('find', 'role', 'button', 'click', '--name', 'Run', '--exact')
  await browser('wait', 'table[aria-label="Query results"] tbody')
  assert.equal(
    (
      await browser(
        'eval',
        `document.querySelector('table[aria-label="Query results"] tbody').rows.length`,
      )
    ).result,
    1,
  )
  assert.match(
    (await browser('get', 'text', 'table[aria-label="Query results"]')).text,
    /answer bigint\s+1\s+7/i,
  )
  await capture('02-query')
  await browser('click', '.cm-content')
  await browser('press', 'Control+A')
  await browser('press', 'Backspace')
  await browser('type', '.cm-content', 'SELECT denied')
  await browser('find', 'role', 'button', 'click', '--name', 'Run', '--exact')
  await browser('wait', '--text', 'Your account does not have permission')
  assert.equal(
    (
      await browser(
        'eval',
        `document.querySelectorAll('table[aria-label="Query results"]').length`,
      )
    ).result,
    0,
  )
  await capture('03-permission-error')
  const mutations = fixture.requests.filter(
    (req) => req.method === 'POST' && req.path === '/api/v1/queries',
  )
  assert.equal(mutations.length, 2)
  assert(
    mutations.every(
      (req) =>
        req.authorization === 'Bearer browser-smoke-user' &&
        req.env === 'staging',
    ),
  )
  assert.equal(JSON.parse(mutations[0].body).sql, 'SELECT 7 AS answer')
  assert.equal(JSON.parse(mutations[1].body).sql, 'SELECT denied')
  writeFileSync(
    resolve(artifactDir, 'results.json'),
    JSON.stringify(
      {
        status: 'passed',
        checks: [
          'built startup',
          'navigation preserving staging',
          'query result',
          'permission failure without stale results',
          'Node bearer identity',
        ],
        requests: fixture.requests.map(
          ({ authorization: _token, ...rest }) => rest,
        ),
      },
      null,
      2,
    ),
  )
  console.log(
    'PASS: built Node app startup, navigation, query, permission failure, staging and caller identity',
  )
} finally {
  try {
    await browser('close')
  } finally {
    app.kill('SIGTERM')
    fixture.server.closeAllConnections()
    await new Promise((resolve) => fixture.server.close(resolve))
    writeFileSync(resolve(artifactDir, 'app.log'), logs)
  }
}
