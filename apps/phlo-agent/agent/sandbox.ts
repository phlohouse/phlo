import {
  agentBrowserRevalidationKey,
  installAgentBrowser,
} from '@agent-browser/eve/sandbox'
import { defineSandbox } from 'eve/sandbox'
import { vercel } from 'eve/sandbox/vercel'

const BEFORE_AFTER_VERSION = '0.0.4'
const REPOSITORY = 'https://github.com/phlohouse/phlo.git'

export default defineSandbox({
  backend: vercel(),
  revalidationKey: () =>
    `phlo-${process.env.VERCEL_GIT_COMMIT_SHA ?? 'local'}:before-and-after-${BEFORE_AFTER_VERSION}:${agentBrowserRevalidationKey()}`,
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
