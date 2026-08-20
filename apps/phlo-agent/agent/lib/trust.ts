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
