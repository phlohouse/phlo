/**
 * Metric tiles for command decks: label, big tabular value, and a note.
 */
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import type { StatusState } from '@/components/observatory/status'
import { HealthDot } from '@/components/observatory/status'
import { cn } from '@/lib/utils'

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
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground flex items-center gap-1.5 text-[11px] font-medium tracking-wide uppercase">
          {icon}
          {label}
        </span>
        {state && <HealthDot state={state} />}
      </div>
      <div className="text-foreground tabular mt-1 font-mono text-2xl font-semibold">
        {value}
      </div>
      {note && (
        <div className="text-muted-foreground mt-0.5 truncate text-[11px]">
          {note}
        </div>
      )}
    </>
  )
  const className = cn(
    'bg-card ring-foreground/10 hover:bg-accent/40 flex flex-col rounded-none p-3 ring-1 transition-colors',
    href && 'cursor-pointer',
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
        'grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6',
        className,
      )}
    >
      {children}
    </div>
  )
}
