/**
 * Shared health/status vocabulary: dot, badge, and label helpers that map
 * Observatory health states onto the status color ramp.
 */
import type { ReactNode } from 'react'

import type { ObservatoryHealthState } from '@/observatory/api/types'
import { cn } from '@/lib/utils'

export type StatusState = ObservatoryHealthState | 'info'

const stateOrder: Record<StatusState, number> = {
  error: 0,
  warning: 1,
  info: 2,
  unknown: 3,
  ok: 4,
}

export function worstStatus(states: Iterable<StatusState>): StatusState {
  let worst: StatusState = 'unknown'
  let worstRank = -1
  for (const state of states) {
    const rank = stateOrder[state] ?? stateOrder.unknown
    if (worstRank === -1 || rank < worstRank) {
      worst = state
      worstRank = rank
    }
  }
  return worstRank === -1 ? 'unknown' : worst
}

export function HealthDot({
  state,
  className,
}: {
  state: StatusState | string | null | undefined
  className?: string
}) {
  return (
    <span
      aria-hidden="true"
      className={cn('status-dot', className)}
      data-state={state ?? 'unknown'}
    />
  )
}

/* State rendered as a tinted band chip — a stamp, not a pill. */
const badgeClassByState: Record<string, string> = {
  ok: 'bg-status-band-ok text-ok-ink',
  warning: 'bg-status-band-warning text-amber-ink',
  error: 'bg-status-band-error text-print-red',
  info: 'bg-status-band-info text-status-info',
  unknown: 'bg-band text-ink-faint',
}

export function StatusBadge({
  state,
  label,
  className,
}: {
  state: StatusState | string | null | undefined
  label?: ReactNode
  className?: string
}) {
  const normalized = state ?? 'unknown'
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-[0.14em] uppercase',
        badgeClassByState[normalized] ?? badgeClassByState.unknown,
        className,
      )}
    >
      <HealthDot className="status-dot-sm" state={normalized} />
      {label ?? normalized}
    </span>
  )
}

/** Map arbitrary status strings (run/operation statuses) onto the ramp. */
export function statusStateFor(value: string | null | undefined): StatusState {
  switch (value) {
    case 'ok':
    case 'running':
    case 'succeeded':
    case 'pass':
    case 'passing':
    case 'published':
    case 'current':
    case 'ready':
    case 'starting':
      return 'ok'
    case 'warning':
    case 'queued':
    case 'skipped':
    case 'draft':
    case 'unhealthy':
      return 'warning'
    case 'error':
    case 'failed':
    case 'failing':
    case 'cancelled':
    case 'stopped':
      return 'error'
    default:
      return 'unknown'
  }
}
