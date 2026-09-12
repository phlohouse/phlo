/**
 * Pulse model for /pulse: merges operations and log events into one
 * chronological stream. Each event carries a state for its marker, a
 * kind for filtering, and a deep link into its workbench.
 */
import type {
  ObservatoryLogEvent,
  ObservatoryOperation,
} from '@/observatory/api/types'
import type { StatusState } from '@/components/observatory/status'
import { isNoisyLog } from '@/observatory/routes/OverviewRoute'

export type PulseKind = 'operation' | 'log'

export interface PulseEvent {
  id: string
  kind: PulseKind
  at: string
  state: StatusState
  title: string
  detail?: string
  source?: string
  resourceLabel?: string
  href: string
  level?: string
}

const OPERATION_STATE: Record<string, StatusState> = {
  failed: 'error',
  running: 'info',
  succeeded: 'ok',
}

function operationEvent(operation: ObservatoryOperation): PulseEvent {
  return {
    id: `op:${operation.id}`,
    kind: 'operation',
    at: operation.completed_at ?? operation.started_at ?? '',
    state:
      OPERATION_STATE[operation.status] ??
      (operation.health.state as StatusState) ??
      'unknown',
    title: operation.name,
    detail: [operation.kind, operation.status].filter(Boolean).join(' · '),
    resourceLabel: operation.target?.label ?? undefined,
    href: `/operations?operationId=${encodeURIComponent(operation.id)}`,
    level: operation.status,
  }
}

function logEvent(log: ObservatoryLogEvent): PulseEvent {
  const level = (log.level ?? 'info').toLowerCase()
  return {
    id: `log:${log.id}`,
    kind: 'log',
    at: log.timestamp ?? '',
    state:
      level === 'error'
        ? 'error'
        : level === 'warning' || level === 'warn'
          ? 'warning'
          : 'info',
    title: log.message,
    source: log.source ?? undefined,
    resourceLabel: log.resource?.label ?? undefined,
    href: `/logs?logId=${encodeURIComponent(log.id)}`,
    level,
  }
}

export function buildPulseStream({
  operations,
  logs,
}: {
  operations: Array<ObservatoryOperation>
  logs: Array<ObservatoryLogEvent>
}): Array<PulseEvent> {
  return [
    ...operations.map(operationEvent),
    // The stream is for signal — infra noise stays in the Logs workbench.
    ...logs.filter((log) => !isNoisyLog(log)).map(logEvent),
  ]
    .filter((event) => event.at)
    .sort((a, b) => b.at.localeCompare(a.at))
}
