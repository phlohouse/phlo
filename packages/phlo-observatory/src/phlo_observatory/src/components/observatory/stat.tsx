/**
 * Figures strip: the printed totals line. Cells sit side by side on one
 * banded row divided by vertical rules — not a grid of cards. A figure's
 * `state` tints its cell band; `href` makes the cell navigable.
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
  unknown: 'bg-sheet',
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
        <span className="text-ink-soft flex items-center gap-1 font-mono text-[9px] font-bold tracking-[0.16em] uppercase">
          {icon}
          {label}
        </span>
        {state && <HealthDot className="status-dot-sm" state={state} />}
      </div>
      <div className="text-ink tabular mt-0.5 font-mono text-xl font-bold">
        {value}
      </div>
      {note && (
        <div className="text-ink-faint mt-0.5 truncate font-mono text-[10px]">
          {note}
        </div>
      )}
    </>
  )
  const className = cn(
    'flex min-w-0 flex-1 flex-col px-3 py-2',
    band,
    href && 'hover:bg-band-strong',
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
        'border-rule bg-sheet divide-rule-soft flex divide-x overflow-x-auto border',
        className,
      )}
    >
      {children}
    </div>
  )
}
