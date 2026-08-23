// Trust predicates used by tool-approval policies. Each answers whether the
// current session identity permits autonomous action or must fall back to
// user approval.
import type { SessionAuthContext } from 'eve/context'

export function autonomousWritesEnabled(): boolean {
  return process.env.PHLO_AGENT_AUTONOMOUS_WRITES === '1'
}

/** True when the session was started by the scheduler itself, not a human channel. */

export function isScheduleAppAuth(auth: SessionAuthContext | null): boolean {
  return auth !== null
    && auth.authenticator === 'app'
    && auth.principalId === 'eve:app'
    && auth.principalType === 'runtime'
}

/**
 * True when the session is a webhook-triggered triage run bound to exactly the
 * issue named by `issueNumber`. Comparing the number keeps a webhook for one
 * issue from authorising writes against another.
 */

export function isGitHubIssueTriageAuth(
  auth: SessionAuthContext | null,
  issueNumber?: number,
): boolean {
  return auth !== null
    && issueNumber !== undefined
    && auth.authenticator === 'github-webhook'
    && auth.principalType === 'user'
    && auth.attributes.repository === 'phlohouse/phlo'
    && auth.attributes.conversation_kind === 'issue'
    && auth.attributes.issue_number === String(issueNumber)
}
