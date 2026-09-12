/**
 * Row-list pattern used across Observatory: a status dot, title, meta line,
 * and a kind/label badge. Works as link or button.
 */
import { Link } from '@tanstack/react-router'
import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'

import type { StatusState } from '@/components/observatory/status'
import { HealthDot } from '@/components/observatory/status'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'

export function RowList({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('divide-y divide-border', className)}>{children}</div>
  )
}

export function RowItem({
  title,
  meta,
  reason,
  state,
  badge,
  href,
  onClick,
  selected,
  actions,
  className,
}: {
  title: ReactNode
  meta?: ReactNode
  reason?: ReactNode
  state?: StatusState | string
  badge?: ReactNode
  href?: string
  onClick?: () => void
  selected?: boolean
  actions?: ReactNode
  className?: string
}) {
  const body = (
    <>
      <HealthDot state={state} className="mt-1" />
      <span className="min-w-0 flex-1">
        <span className="text-foreground block truncate text-xs font-medium">
          {title}
        </span>
        {meta && (
          <span className="text-muted-foreground mt-0.5 block truncate font-mono text-[11px]">
            {meta}
          </span>
        )}
        {reason && (
          <span className="text-muted-foreground mt-0.5 block truncate text-[11px]">
            {reason}
          </span>
        )}
      </span>
      {badge && (
        <Badge className="font-mono text-[10px]" variant="secondary">
          {badge}
        </Badge>
      )}
      {actions}
      {(href || onClick) && !actions && (
        <ChevronRight className="text-muted-foreground size-3.5 flex-none" />
      )}
    </>
  )
  const itemClass = cn(
    'hover:bg-accent/50 flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors',
    selected && 'bg-accent/60 hover:bg-accent/60',
    className,
  )
  if (href) {
    return (
      <Link className={itemClass} to={href}>
        {body}
      </Link>
    )
  }
  return (
    <button className={itemClass} onClick={onClick} type="button">
      {body}
    </button>
  )
}
