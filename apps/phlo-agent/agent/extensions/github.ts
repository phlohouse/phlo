/**
 * GitHub tool extension gated by approval policies: scheduled write sessions
 * must be the scheduler app with autonomous writes enabled and phlohouse/phlo
 * targets, and issue-triage labelling is further restricted to the triggering
 * issue's webhook session and the triage label allowlist.
 */
import githubExtension from '@github-tools/eve-extension'
import type { ApprovalContext, ApprovalStatus } from 'eve/tools'
import { GITHUB_CONNECTOR } from '../lib/github/credentials'
import { issueTriageLabelsAllowed } from '../lib/github/triage'
import {
  autonomousWritesEnabled,
  isGitHubIssueTriageAuth,
  isScheduleAppAuth,
} from '../lib/trust'

function requireUserApproval(): ApprovalStatus {
  return 'user-approval'
}

function targetsPhlo(input: unknown): boolean {
  const target = input as { owner?: unknown; repo?: unknown } | undefined
  const owner = target?.owner === undefined ? 'phlohouse' : String(target.owner).toLowerCase()
  const repo = target?.repo === undefined ? 'phlo' : String(target.repo).toLowerCase()
  return owner === 'phlohouse' && repo === 'phlo'
}

/**
 * Approval policy for write tools invoked by scheduled runs. Only the
 * scheduler app identity may proceed, autonomous writes must be enabled
 * globally, and targets are restricted to phlohouse/phlo. Returning
 * 'not-applicable' lets the call run without prompting; 'user-approval' blocks
 * until a human approves, and 'denied' refuses outright.
 */
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

/**
 * Approval policy for addLabels during issue triage. Scheduled sessions fall
 * back to the scheduler policy; webhook sessions pass only when they belong to
 * the exact issue being relabelled and the labels satisfy the triage
 * allowlist.
 */
function issueTriageWrite(ctx: ApprovalContext): ApprovalStatus {
  if (isScheduleAppAuth(ctx.session.auth.current)) return scheduledWrite(ctx)
  if (!autonomousWritesEnabled()) {
    return {
      type: 'denied',
      reason: 'Autonomous writes are disabled. Set PHLO_AGENT_AUTONOMOUS_WRITES=1 to enable them.',
    }
  }
  if (!targetsPhlo(ctx.toolInput)) {
    return { type: 'denied', reason: 'Issue triage writes are restricted to phlohouse/phlo.' }
  }
  const input = ctx.toolInput as { issueNumber?: unknown; labels?: unknown } | undefined
  const issueNumber = typeof input?.issueNumber === 'number' ? input.issueNumber : undefined
  if (
    !isGitHubIssueTriageAuth(ctx.session.auth.current, issueNumber)
    || !issueTriageLabelsAllowed(input?.labels)
  ) {
    return requireUserApproval()
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
    'listLabels',
    'addLabels',
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
    addLabels: issueTriageWrite,
    createIssue: scheduledWrite,
    createPullRequest: (ctx: ApprovalContext): ApprovalStatus => {
      // Scheduled runs may open drafts only; every other pull request needs a
      // human approval.
      const input = ctx.toolInput as { draft?: unknown } | undefined
      if (isScheduleAppAuth(ctx.session.auth.current) && input?.draft === true) {
        return scheduledWrite(ctx)
      }
      return requireUserApproval()
    },
  },
})
