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

const NOISE_NEEDLES = [
  'failed_to_discover_user_workflows',
  'hasura_using_generated_default_admin_secret',
  'no heartbeat received',
  'optional_capability_degraded',
  'unknown_plugin_type',
  'plugin_load_failed',
  'plugin_registry_fetch_fallback',
  'observatory_settings_falling_back_to_memory',
  'observatory_settings_storage_unavailable',
  'using the generated default hasura admin secret',
  'workflows directory not found',
  'syntaxwarning',
  'py.warnings',
]

/** Infra chatter that drowns the stream; the full log stays queryable. */
export function isNoisyLog(log: ObservatoryLogEvent): boolean {
  const message = log.message.toLowerCase()
  const source = log.source?.toLowerCase() ?? ''
  const event = String(log.metadata?.event ?? '').toLowerCase()
  return NOISE_NEEDLES.some(
    (needle) =>
      message.includes(needle) ||
      source.includes(needle) ||
      event.includes(needle),
  )
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
    // The stream is for signal — infra noise stays out of the deck.
    ...logs.filter((log) => !isNoisyLog(log)).map(logEvent),
  ]
    .filter((event) => event.at)
    .sort((a, b) => b.at.localeCompare(a.at))
}
