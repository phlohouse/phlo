import type { SessionAuthContext } from 'eve/context'

export function autonomousWritesEnabled(): boolean {
  return process.env.PHLO_AGENT_AUTONOMOUS_WRITES === '1'
}

export function isScheduleAppAuth(auth: SessionAuthContext | null): boolean {
  return auth !== null
    && auth.authenticator === 'app'
    && auth.principalId === 'eve:app'
    && auth.principalType === 'runtime'
}

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
