/**
 * Sandbox definition: a Vercel-backed sandbox bootstrapped with the phlo repo
 * clone, git identity, uv/Python, and pinned tooling. A versioned revalidation
 * key invalidates stale snapshots when bootstrap inputs change, and each
 * session re-points the clone at origin/main for a clean checkout.
 */
import {
  agentBrowserRevalidationKey,
  installAgentBrowser,
} from '@agent-browser/eve/sandbox'
import { defineSandbox } from 'eve/sandbox'
import { vercel } from 'eve/sandbox/vercel'

const BEFORE_AFTER_VERSION = '0.0.4'
const REPOSITORY = 'https://github.com/phlohouse/phlo.git'
// Bump when the sandbox bootstrap changes in a way that requires a fresh template.
const SANDBOX_TEMPLATE_VERSION = '1'

export default defineSandbox({
  backend: vercel({
    keepLastSnapshots: {
      count: 1,
      deleteEvicted: true,
    },
  }),
  // Snapshots are reused only while this key is unchanged; bumping any
  // embedded version (template, pinned tool versions) invalidates previously
  // captured sandboxes so the next bootstrap starts from fresh inputs.
  revalidationKey: () =>
    `phlo:${SANDBOX_TEMPLATE_VERSION}:before-and-after-${BEFORE_AFTER_VERSION}:${agentBrowserRevalidationKey()}`,
  async bootstrap({ use }) {
    const sandbox = await use()
    await sandbox.run({
      command: `git clone --depth 50 ${REPOSITORY} /workspace/repo`,
    })
    await sandbox.run({
      command: [
        'git config --global user.name "phlo-agent[bot]"',
        'git config --global user.email "phlo-agent[bot]@users.noreply.github.com"',
      ].join(' && '),
    })
    await sandbox.run({
      command: 'curl -LsSf https://astral.sh/uv/0.12.1/install.sh | sh',
    })
    await sandbox.run({
      command: [
        'cd /workspace/repo',
        '$HOME/.local/bin/uv python install 3.11',
        '$HOME/.local/bin/uv sync --python 3.11 --dev --locked',
      ].join(' && '),
    })
    await sandbox.run({
      command: 'npm ci --prefix /workspace/repo/packages/phlo-observatory/src/phlo_observatory',
    })
    await installAgentBrowser(sandbox)
    await sandbox.run({
      command: `npm install --global @vercel/before-and-after@${BEFORE_AFTER_VERSION}`,
    })
  },

  // Re-point the clone at origin/main at the start of every session so each
  // one begins from a clean, up-to-date checkout instead of whatever the last
  // session left on disk.
  async onSession({ use }) {
    const sandbox = await use()
    await sandbox.run({
      command: [
        'git config --global --add safe.directory /workspace',
        'git config --global --add safe.directory /workspace/repo',
        'git -C /workspace/repo fetch origin main',
        'git -C /workspace/repo checkout -B main origin/main',
      ].join(' && '),
    })
  },
})
