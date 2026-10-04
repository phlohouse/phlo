/** Regression tests for Phlo agent tool permissions and webhook routing. */
import assert from 'node:assert/strict'
import { createHmac } from 'node:crypto'
import test from 'node:test'
import registerPlugin from './index.ts'

test('maintenance can read schedules and only the host gets operational tools', async () => {
  const modes = new Map()
  await registerPlugin({
    registerSkill: async () => {},
    createAgent: (definition) => ({ definition }),
    registerAgentMode: (mode) => modes.set(mode.key, mode.agent),
    registerTool: () => {},
    createWebhook: async () => {},
    logger: { log: () => {} },
  })

  const maintenance = modes.get('phlo-maintenance')
  const host = modes.get('phlo-automation-host')
  const reviewer = modes.get('phlo-review')
  for (const tool of ['get_schedule', 'tool_search', 'code_exec']) {
    assert.ok(maintenance.tools.includes(tool), `maintenance needs ${tool}`)
  }
  assert.match(maintenance.instructions, /amp\.get_schedule/)
  for (const tool of ['shell_command', 'reload_plugins', 'read_thread', 'send_thread_message']) {
    assert.ok(host.tools.includes(tool), `host needs ${tool}`)
    assert.ok(!maintenance.tools.includes(tool), `maintenance must not get ${tool}`)
    assert.ok(!reviewer.tools.includes(tool), `reviewer must not get ${tool}`)
  }
  assert.deepEqual(reviewer.tools, [
    'Read', 'finder', 'librarian', 'read_web_page', 'web_search', 'skill',
    'plugin__phlo-github__publish_phlo_github_comment',
    'plugin__phlo-github__update_phlo_pull_request',
  ])
  assert.ok(maintenance.tools.includes('plugin__phlo-github__publish_phlo_maintenance_*'))
  assert.ok(!host.tools.some((tool) => tool.startsWith('plugin__')))
})

test('signed webhook events still create reviewers rather than operational hosts', async (t) => {
  const secret = 'test-webhook-secret'.repeat(2)
  const previousEnv = process.env
  process.env = {
    ...previousEnv,
    PHLO_GITHUB_WEBHOOK_SECRET: secret,
    PHLO_GITHUB_WRITER_TOKEN: 'test-writer-token'.repeat(2),
    PHLO_GITHUB_WRITER_URL: 'https://writer.example.test',
  }
  t.after(() => { process.env = previousEnv })
  let webhook
  const created = []
  const messages = []
  await registerPlugin({
    registerSkill: async () => {},
    createAgent: (definition) => ({
      definition,
      createThread: async (options) => {
        created.push({ definition, options })
        return { id: 'T-review', appendUserMessage: async (message) => messages.push(message) }
      },
    }),
    registerAgentMode: () => {},
    registerTool: () => {},
    createWebhook: async (options) => { webhook = options },
    configuration: {
      get: async () => ({ phloGitHubAutomationHost: 'T-host' }),
      update: async () => {},
    },
    logger: { log: () => {} },
  })
  const body = Buffer.from(JSON.stringify({
    action: 'opened',
    repository: { full_name: 'phlohouse/phlo' },
    sender: { type: 'User' },
    issue: { number: 17 },
  }))
  await webhook.handler({
    body,
    headers: {
      'x-github-delivery': 'test-delivery',
      'x-github-event': 'issues',
      'x-hub-signature-256': `sha256=${createHmac('sha256', secret).update(body).digest('hex')}`,
    },
    receivedAt: '2026-10-04T12:00:00.000Z',
  }, { thread: { id: 'T-owner' }, logger: { log: () => {} } })
  assert.equal(created.length, 1)
  assert.equal(created[0].definition.display.label, 'Phlo review')
  assert.equal(created[0].options.parentThreadID, 'T-host')
  assert.equal(created[0].definition.tools.includes('shell_command'), false)
  assert.equal(messages.length, 1)
  assert.match(messages[0].content, /Phlo issue #17/)
})
