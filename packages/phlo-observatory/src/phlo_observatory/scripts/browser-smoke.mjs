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
  fixture.controls.delayMs = 1_500
  await browser(
    'click',
    'nav[aria-label="Primary navigation"] a[href="/query?env=staging"]',
  )
  await browser('wait', '[aria-label="Loading"][aria-busy="true"]')
  await capture('01b-loading')
  await browser('wait', '.cm-content')
  fixture.controls.delayMs = 0
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

  // These are controlled HTTP failures, not evidence about a deployed backend.
  const storageFailure = {
    status: 503,
    body: {
      error: {
        code: 'backend_unavailable',
        message:
          'Incident storage schema is unavailable. Apply the API schema migrations, then check again.',
      },
    },
  }
  fixture.controls.overrides.set('/api/v1/incidents', storageFailure)
  fixture.controls.overrides.set('/api/v1/overview', storageFailure)
  await browser('open', `${base}/?env=staging`)
  await browser('wait', '--text', 'Partial observations')
  assert.match((await browser('get', 'text', 'main')).text, /warehouse_orders/)
  assert.match(
    (await browser('get', 'text', 'main')).text,
    /Reference: fixture-readiness-1129/,
  )
  assert.match(
    (await browser('get', 'text', 'nav[aria-label="Primary navigation"]')).text,
    /Incident status is unavailable/,
  )
  await capture('04-schema-failure-independent-data')

  fixture.controls.overrides.set('/api/v1/environments', {
    body: {
      items: [
        { env: 'staging', status: 'unavailable' },
        { env: 'prod', status: 'available' },
      ],
      default_environment: 'staging',
    },
  })
  await browser('open', `${base}/`)
  await browser('wait', '--text', 'Partial observations')
  assert.match((await browser('get', 'url')).url, /env=staging/)
  assert.match((await browser('get', 'text', 'main')).text, /warehouse_orders/)
  await capture('04b-unavailable-default-retained')

  const catalogRequests = () =>
    fixture.requests.filter(
      (request) => request.path === '/api/v1/query/catalog',
    ).length
  const beforeSetup = catalogRequests()
  fixture.controls.overrides.set('/api/v1/readiness', {
    body: {
      env: 'staging',
      observed_at: new Date().toISOString(),
      correlation_id: 'fixture-readiness-1129',
      capabilities: {
        query_preview: [
          {
            state: 'disabled',
            reason_code: 'intentionally_disabled',
            message: 'Query preview is not configured.',
            remediation:
              'Provision a dedicated preview coordinator and check again.',
          },
        ],
        orchestration: [
          {
            state: 'unavailable',
            reason_code: 'code_location_unavailable',
            message: 'Configured code location is not loaded.',
            remediation:
              'Load the configured Dagster code location, then check again.',
          },
        ],
        incidents: [
          {
            state: 'unavailable',
            reason_code: 'schema_missing',
            message: 'Required storage schema is missing or outdated.',
            remediation:
              'Apply the documented API schema migrations, then check again.',
          },
        ],
      },
    },
  })
  await browser('open', `${base}/query?env=staging`)
  await browser('wait', '--text', 'Query setup')
  assert.equal(
    catalogRequests(),
    beforeSetup,
    'Disabled preview must not request catalogs',
  )
  await capture('05-unconfigured-preview')
  await browser('open', `${base}/settings?env=staging`)
  await browser('wait', '--text', 'code_location_unavailable')
  assert.match((await browser('get', 'text', 'main')).text, /schema_missing/)
  await browser(
    'eval',
    `Array.from(document.querySelectorAll('h3')).find((element) => element.textContent === 'orchestration').scrollIntoView({block:'center'})`,
  )
  await capture('06-missing-location-readiness')

  fixture.controls.overrides.clear()
  fixture.controls.overrides.set('/api/v1/services', {
    status: 502,
    body: {
      error: {
        code: 'bad_gateway',
        message: 'Service observations are unavailable.',
      },
    },
  })
  await browser('open', `${base}/?env=staging`)
  await browser('wait', '--text', 'Services unavailable')
  await browser('click', 'main > details > summary')
  assert.match((await browser('get', 'text', 'main')).text, /0 of 7 fresh/)
  await capture('07-backend-outage')
  fixture.controls.overrides.clear()
  await browser(
    'find',
    'role',
    'button',
    'click',
    '--name',
    'Check again',
    '--exact',
  )
  await browser(
    'wait',
    '--fn',
    `!document.querySelector('main').textContent.includes('Services unavailable')`,
  )
  assert.match((await browser('get', 'text', 'main')).text, /0 of 7 fresh/)
  await capture('08-recovered-without-restart')
  await browser('set', 'viewport', '390', '844', '2')
  assert.equal(
    (
      await browser(
        'eval',
        'document.documentElement.scrollWidth <= innerWidth',
      )
    ).result,
    true,
  )
  await capture('09-narrow-recovered')
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
          'missing schema preserves independent data and navigation',
          'disabled preview skips catalog calls',
          'missing code location and schema readiness reasons',
          'backend outage retries visibly without process restart',
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
