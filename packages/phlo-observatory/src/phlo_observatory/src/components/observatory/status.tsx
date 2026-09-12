/**
 * Shared health/status vocabulary: dot, badge, and label helpers that map
 * Observatory health states onto the status color ramp.
 */
import type { ReactNode } from 'react'

import type { ObservatoryHealthState } from '@/observatory/api/types'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'

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

const badgeVariantByState: Record<
  string,
  'default' | 'secondary' | 'destructive' | 'outline'
> = {
  ok: 'secondary',
  warning: 'outline',
  error: 'destructive',
  info: 'secondary',
  unknown: 'secondary',
}

const badgeClassByState: Record<string, string> = {
  ok: 'border-status-ok/40 bg-status-ok/10 text-status-ok',
  warning: 'border-status-warning/40 bg-status-warning/10 text-status-warning',
  error: 'border-status-error/40 bg-status-error/10 text-status-error',
  info: 'border-status-info/40 bg-status-info/10 text-status-info',
  unknown: 'border-border bg-muted text-muted-foreground',
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
    <Badge
      className={cn(
        'gap-1.5 font-mono text-[10px] tracking-wide uppercase',
        badgeClassByState[normalized] ?? badgeClassByState.unknown,
        className,
      )}
      variant={badgeVariantByState[normalized] ?? 'secondary'}
    >
      <HealthDot state={normalized} className="status-dot-sm" />
      {label ?? normalized}
    </Badge>
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
