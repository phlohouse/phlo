/**
 * Figures strip: headline metrics as a grid of elevated cards. A figure's
 * `state` sets its status dot; `href` makes the card navigable.
 */
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import type { StatusState } from '@/components/observatory/status'
import { HealthDot } from '@/components/observatory/status'
import { cn } from '@/lib/utils'

const bandByState: Record<string, string> = {
  ok: 'bg-status-band-ok',
  warning: 'bg-status-band-warning',
  error: 'bg-status-band-error',
  info: 'bg-status-band-info',
  unknown: 'bg-panel',
}

export function StatCard({
  label,
  value,
  note,
  icon,
  state,
  href,
}: {
  label: ReactNode
  value: ReactNode
  note?: ReactNode
  icon?: ReactNode
  state?: StatusState | string
  href?: string
}) {
  const band = bandByState[state ?? 'unknown'] ?? bandByState.unknown
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-ink-soft flex items-center gap-1.5 text-xs font-medium">
          {icon}
          {label}
        </span>
        {state && <HealthDot className="status-dot-sm" state={state} />}
      </div>
      <div className="text-ink tabular text-xl font-semibold tracking-tight">
        {value}
      </div>
      {note && <div className="text-ink-faint truncate text-xs">{note}</div>}
    </>
  )
  const className = cn(
    'border-rule flex min-w-0 flex-1 flex-col gap-1 rounded-xl border px-3.5 py-3 transition-colors',
    band,
    href && 'hover:border-ink-faint/40 hover:bg-raised',
  )
  if (href) {
    return (
      <Link className={className} to={href}>
        {body}
      </Link>
    )
  }
  return <div className={className}>{body}</div>
}

export function StatGrid({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6',
        className,
      )}
    >
      {children}
    </div>
  )
}
