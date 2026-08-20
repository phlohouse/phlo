import githubExtension from '@github-tools/eve-extension'
import type { ApprovalContext, ApprovalStatus } from 'eve/tools'
import { GITHUB_CONNECTOR } from '../lib/github/credentials'
import { autonomousWritesEnabled, isScheduleAppAuth } from '../lib/trust'

function requireUserApproval(): ApprovalStatus {
  return 'user-approval'
}

function targetsPhlo(input: unknown): boolean {
  const target = input as { owner?: unknown; repo?: unknown } | undefined
  const owner = target?.owner === undefined ? 'phlohouse' : String(target.owner).toLowerCase()
  const repo = target?.repo === undefined ? 'phlo' : String(target.repo).toLowerCase()
  return owner === 'phlohouse' && repo === 'phlo'
}

function scheduledWrite(ctx: ApprovalContext): ApprovalStatus {
  if (!isScheduleAppAuth(ctx.session.auth.current)) return requireUserApproval()
  if (!autonomousWritesEnabled()) {
    return {
      type: 'denied',
      reason: 'Autonomous writes are disabled. Set PHLO_AGENT_AUTONOMOUS_WRITES=1 to enable them.',
    }
  }
  if (!targetsPhlo(ctx.toolInput)) {
    return { type: 'denied', reason: 'Scheduled writes are restricted to phlohouse/phlo.' }
  }
  return 'not-applicable'
}

export default githubExtension({
  connector: GITHUB_CONNECTOR,
  context: { owner: 'phlohouse', repo: 'phlo' },
  include: [
    'getRepository',
    'getRepositoryTree',
    'getFileContent',
    'searchCode',
    'getBlame',
    'listCommits',
    'searchIssues',
    'listIssues',
    'getIssueContext',
    'createIssue',
    'listPullRequests',
    'getPullRequestContext',
    'listPullRequestFiles',
    'createPullRequest',
    'listReleases',
    'listCheckRuns',
    'getCiFailureContext',
  ],
  requireApproval: {
    createIssue: scheduledWrite,
    createPullRequest: (ctx: ApprovalContext): ApprovalStatus => {
      const input = ctx.toolInput as { draft?: unknown } | undefined
      if (isScheduleAppAuth(ctx.session.auth.current) && input?.draft === true) {
        return scheduledWrite(ctx)
      }
      return requireUserApproval()
    },
  },
})
