/**
 * Git tools for the agent. Registers the branch push tool only for scheduler
 * sessions with autonomous writes enabled; pushes validate the ref, route
 * sandbox egress through a token-injecting broker policy, and restore access
 * afterwards.
 */
import { defineDynamic, defineTool } from 'eve/tools'
import { z } from 'zod'
import { githubCredentials } from '../lib/github/credentials'
import {
  mintInstallationToken,
  pushBrokerPolicy,
  validatePushBranch,
} from '../lib/github/push'
import { autonomousWritesEnabled, isScheduleAppAuth } from '../lib/trust'
import { REPO_DIR, runOutput } from '../lib/workspace'

const PUSH_URL = 'https://github.com/phlohouse/phlo.git'

export default defineDynamic({
  events: {
    'turn.started': (_event, ctx) => {
      // Register the push tool only for scheduler sessions with autonomous
      // writes enabled; other sessions never see the tool at all.
      if (!isScheduleAppAuth(ctx.session.auth.current) || !autonomousWritesEnabled()) return null
      return {
        git__push: defineTool({
          description: `Push a committed feature branch from ${REPO_DIR}. Direct pushes to main and master are refused.`,
          inputSchema: z.object({ branch: z.string().min(1) }),
          async execute(input, toolCtx) {
            // Trust is re-checked at execution time rather than relying on
            // the registration gate above alone.
            if (!isScheduleAppAuth(toolCtx.session.auth.current) || !autonomousWritesEnabled()) {
              return {
                success: false as const,
                error: 'Scheduled maintenance pushing is not enabled.',
              }
            }
            const refusal = validatePushBranch(input.branch)
            if (refusal) return { success: false as const, error: refusal }

            // Scope sandbox egress to the push broker for the duration of
            // the push, then restore unrestricted access. The finally clause
            // keeps a failed git push from stranding later tool calls behind
            // the restricted policy.
            const sandbox = await toolCtx.getSandbox()
            const token = await mintInstallationToken(githubCredentials)
            await sandbox.setNetworkPolicy(pushBrokerPolicy(token))
            try {
              const push = await sandbox.run({
                command: `git -C ${REPO_DIR} push ${PUSH_URL} 'refs/heads/${input.branch}:refs/heads/${input.branch}'`,
              })
              if (push.exitCode !== 0) {
                return {
                  success: false as const,
                  error: `git push exited ${push.exitCode}: ${runOutput(push)}`,
                }
              }
              const head = await sandbox.run({
                command: `git -C ${REPO_DIR} rev-parse '${input.branch}'`,
              })
              return {
                success: true as const,
                branch: input.branch,
                sha: String(head.stdout).trim(),
                repository: 'phlohouse/phlo',
              }
            } finally {
              await sandbox.setNetworkPolicy('allow-all')
            }
          },
        }),
      }
    },
  },
})
