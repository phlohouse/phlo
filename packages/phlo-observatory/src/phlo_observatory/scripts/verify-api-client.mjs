/** Exercises the Observatory API client against a disposable browser fixture. */
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync, readdirSync } from 'node:fs'

// Run against the disposable commerce fixture: one prod asset, two staging assets,
// production_orders with 3 rows on main, staging_orders with 7 rows on candidate.
// PHLO_TEST_TOKEN=<token> node scripts/verify-api-client.mjs <app-url> 1 2
const [base, prodCount, stagingCount] = process.argv.slice(2)
assert(
  base && prodCount && stagingCount,
  'Supply app URL and independent prod/staging asset counts.',
)
assert.notEqual(
  prodCount,
  stagingCount,
  'Use asymmetric environment inventories.',
)
assert(
  process.env.PHLO_TEST_TOKEN,
  'Supply a disposable user token in PHLO_TEST_TOKEN.',
)
const session = `api-client-${process.pid}`

async function checkMountedApiRoutes() {
  if (!process.env.PHLO_TEST_API_URL) return
  const endpoint = new URL(
    'openapi.json',
    `${process.env.PHLO_TEST_API_URL.replace(/\/$/, '')}/`,
  )
  const response = await fetch(endpoint, {
    headers: { Authorization: `Bearer ${process.env.PHLO_TEST_TOKEN}` },
  })
  assert(response.ok, `OpenAPI request failed (${response.status}).`)
  const document = await response.json()
  assert(
    document && typeof document.paths === 'object',
    'OpenAPI response has no paths object.',
  )

  const normalize = (path) =>
    `/${path
      .split('?')[0]
      .replace(/^\//, '')
      .replace(/\$\{[^}]+\}|\{[^}]+\}/g, '{}')}`
  const mounted = new Set(Object.keys(document.paths).map(normalize))
  const apiDirectory = new URL('../src/lib/data/api/', import.meta.url)
  const literals = readdirSync(apiDirectory)
    .filter((name) => name.endsWith('.ts'))
    .flatMap((name) => {
      const source = readFileSync(new URL(name, apiDirectory), 'utf8')
      return [
        ...source.matchAll(/phloApi\(\s*([`'"])(\/?api\/v1\/[^`'"]+)\1/g),
      ].map((match) => match[2])
    })
  assert(literals.length > 0, 'No literal client API routes were found.')
  const missing = [
    ...new Set(literals.map(normalize).filter((path) => !mounted.has(path))),
  ].sort()
  assert.deepEqual(
    missing,
    [],
    `Client API routes missing from OpenAPI: ${missing.join(', ')}`,
  )
  console.log(
    `PASS: ${new Set(literals.map(normalize)).size} literal client route shapes are mounted in live OpenAPI.`,
  )
}

function browser(...args) {
  let output
  try {
    output = execFileSync(
      'agent-browser',
      ['--session', session, ...args, '--json'],
      {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    )
  } catch {
    throw new Error(
      'Browser command failed; diagnostics withheld to protect credentials.',
    )
  }
  const result = JSON.parse(output)
  assert(result.success, 'Browser command failed.')
  return result.data
}

function check(env, count) {
  browser('wait', '--text', `Overview · ${env}`)
  assert.equal(
    browser('eval', 'document.querySelector("h1").textContent').result,
    `Overview · ${env}`,
  )
  const counts = browser('get', 'text', '[aria-label="Live counts"]').text
  assert.match(
    counts,
    new RegExp(`^ASSETS\\s+${count}\\s+MATERIALIZED ASSETS`, 'i'),
  )
  const statuses = browser('get', 'text', 'nav[aria-label="Primary"]').text
  assert.match(statuses, /unknown/)
  assert.doesNotMatch(statuses, /\bslow\b/)
}

function checkPage(path, env, text, selector = 'h1') {
  browser('open', `${base}${path}?env=${env}`)
  browser('wait', '--load', 'networkidle')
  browser('wait', '--text', text)
  browser('wait', '500')
  assert.match(browser('get', 'text', selector).text, new RegExp(text, 'i'))
  assert.equal(
    browser('eval', 'document.querySelectorAll("[role=alert]").length').result,
    0,
    `${path} rendered an alert.`,
  )
  const page = browser('get', 'text', 'body').text
  assert.doesNotMatch(
    page,
    /fixture(?: warning| data| fallback)?/i,
    `${path} rendered fixture content.`,
  )
  assert.doesNotMatch(
    page,
    /Phlo API (?:request failed|returned invalid JSON|response did not match|is unreachable)|Route Error|Unexpected Application Error/i,
    `${path} rendered a route error.`,
  )
}

await checkMountedApiRoutes()

try {
  browser(
    '--headers',
    JSON.stringify({ Authorization: `Bearer ${process.env.PHLO_TEST_TOKEN}` }),
    'open',
    `${base}/?env=staging`,
  )
  browser('set', 'viewport', '1280', '800', '2')
  browser('wait', '--load', 'networkidle')
  browser(
    'wait',
    '--fn',
    'Object.keys(document.querySelector("nav[aria-label=Primary] button")).some(key => key.startsWith("__reactProps$"))',
  )
  check('staging', stagingCount)
  for (const [env, count] of [
    ['prod', prodCount],
    ['staging', stagingCount],
  ]) {
    browser('click', 'button[aria-haspopup="menu"]')
    browser('wait', `[role="menuitem"][href="/?env=${env}"]`)
    browser('click', `[role="menuitem"][href="/?env=${env}"]`)
    browser('wait', '--fn', '!document.querySelector("[role=menu]")')
    check(env, count)
    browser('find', 'role', 'button', 'click', '--name', 'Refresh')
    check(env, count)
  }
  const links = browser(
    'eval',
    'Array.from(document.querySelectorAll("nav[aria-label=Primary] a")).map(link => link.search)',
  ).result
  assert(
    links.length > 0 && links.every((search) => search === '?env=staging'),
    'Navigation must preserve staging.',
  )
  browser('find', 'role', 'button', 'click', '--name', 'Quick actions ⌘K')
  browser('wait', '[role="dialog"]')
  const commands = browser('get', 'text', '[role="dialog"]').text
  assert.match(commands, /Navigation/)
  assert.doesNotMatch(commands, /bioreactor|Re-run|Pause|#214/)
  browser('find', 'role', 'option', 'click', '--name', 'Overview')
  browser('wait', '--fn', '!document.querySelector("[role=dialog]")')
  check('staging', stagingCount)
  for (const [env, asset, rows, ref] of [
    ['prod', 'production_orders', 3, 'main'],
    ['staging', 'staging_orders', 7, 'candidate'],
  ]) {
    browser('open', `${base}/assets?env=${env}`)
    browser('wait', 'table[aria-label="Assets"] tbody')
    const inventory = browser('get', 'text', 'table[aria-label="Assets"]').text
    assert.match(inventory, new RegExp(`raw/${asset}`))
    assert.doesNotMatch(
      inventory,
      env === 'prod' ? /raw\/staging_/ : /raw\/production_/,
    )
    browser('open', `${base}/assets/raw%2F${asset}?env=${env}&tab=data`)
    browser('wait', 'table[aria-label="Asset preview"] tbody')
    assert.equal(
      browser('eval', 'document.querySelector("main table tbody").rows.length')
        .result,
      rows,
    )
    const preview = browser('get', 'text', 'main').text
    assert.match(preview, new RegExp(`${rows} preview rows · ref ${ref}`))
    assert.match(preview, /O-20260701-0001/)
    assert.doesNotMatch(preview, /fixture-only/)
    browser('open', `${base}/assets/raw%2F${asset}?env=${env}&tab=snapshots`)
    browser('wait', 'table[aria-label="Iceberg snapshots"] tbody')
    const snapshot = browser(
      'eval',
      'document.querySelector("main table tbody td").textContent',
    ).result
    assert.match(snapshot, /^\d{16,19}$/)
    browser('open', `${base}/pipelines?env=${env}`)
    browser('wait', 'table[aria-label="Jobs"] tbody')
    assert.match(
      browser('get', 'text', 'table[aria-label="Jobs"]').text,
      /__ASSET_JOB/,
    )
    browser('open', `${base}/pipelines/timeline?env=${env}`)
    browser('wait', '[aria-label="Timeline for __ASSET_JOB"]')
    assert.equal(
      browser(
        'eval',
        'document.querySelector("[aria-label^=Timeline]").children.length',
      ).result,
      48,
    )
    const runLinks = browser(
      'eval',
      'Array.from(document.querySelectorAll("[aria-label^=Timeline] a")).map(link => new URLSearchParams(link.search).get("env"))',
    ).result
    assert(runLinks.length > 0 && runLinks.every((value) => value === env))
    browser('open', `${base}/query?env=${env}`)
    browser('wait', '--load', 'networkidle')
    browser('wait', '.cm-content')
    browser('click', '.cm-content')
    browser('press', 'Control+A')
    browser('press', 'Backspace')
    browser(
      'type',
      '.cm-content',
      `SELECT order_id, total_amount FROM raw.${asset} ORDER BY order_id`,
    )
    browser('find', 'role', 'button', 'click', '--name', 'Run', '--exact')
    browser('wait', 'table[aria-label="Query results"] tbody')
    assert.equal(
      browser('eval', 'document.querySelector("table tbody").rows.length')
        .result,
      rows,
    )
    assert.match(
      browser('get', 'text', 'table[aria-label="Query results"]').text,
      /O-20260701-0001/,
    )
    browser('find', 'role', 'button', 'click', '--name', 'Explain', '--exact')
    browser(
      'wait',
      '--fn',
      'document.querySelector("[aria-label=Workspace] pre")?.textContent.includes("TableScan")',
    )
    assert.equal(
      browser(
        'eval',
        'document.querySelectorAll("[aria-label=Workspace] table").length',
      ).result,
      0,
    )
  }
  browser('open', `${base}/assets/raw%2Fstaging_customers?env=staging`)
  browser('wait', '--text', 'No materialization observed')
  assert.match(
    browser('get', 'text', 'main').text,
    /No column schema has been observed/,
  )
  browser('open', `${base}/?env=staging`)
  check('staging', stagingCount)
  for (const env of ['prod', 'staging']) {
    checkPage('/incidents', env, 'Incidents')
    checkPage('/query', env, 'Query')
    checkPage('/branches', env, 'Branches')
    checkPage(
      '/staging',
      env,
      env === 'prod'
        ? 'Staging controls are not available from prod'
        : 'Staging cutover',
      'main',
    )
    checkPage('/settings', env, 'Settings')
    checkPage('/settings/members', env, 'Members and access')
    checkPage('/settings/audit-log', env, 'Audit log')
  }
  browser('set', 'viewport', '390', '844', '2')
  const mobileLinks = browser(
    'eval',
    'Array.from(Array.from(document.querySelectorAll("nav[aria-label=Primary]")).at(-1).querySelectorAll("a")).map(link => link.search)',
  ).result
  assert.equal(mobileLinks.length, 4)
  assert(
    mobileLinks.every((search) => search === '?env=staging'),
    'Mobile navigation must preserve staging.',
  )
  console.log(
    'PASS: asymmetric commerce evidence plus Incidents, Query, Branches, Staging, Settings, Members, and Audit loaders in prod and staging; no fixture warnings or route errors.',
  )
} finally {
  browser('close')
}
