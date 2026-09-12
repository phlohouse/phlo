/**
 * Row list: the core collection unit — dense single-line rows inside a
 * rounded panel, separated by soft rules. A row's `state` tints its
 * background; selection uses the selected surface.
 */
import { Link } from '@tanstack/react-router'
import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'

import type { StatusState } from '@/components/observatory/status'
import { HealthDot } from '@/components/observatory/status'
import { cn } from '@/lib/utils'

export function RowList({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'divide-rule-soft border-rule bg-panel divide-y overflow-hidden rounded-xl border',
        className,
      )}
    >
      {children}
    </div>
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
      <HealthDot className="status-dot-sm" state={state} />
      <span className="text-ink truncate text-[13px] font-medium">{title}</span>
      {meta && (
        <span className="text-ink-faint hidden truncate font-mono text-[11px] md:inline">
          {meta}
        </span>
      )}
      {reason && (
        <span className="text-ink-faint hidden min-w-0 flex-1 truncate text-xs lg:inline">
          {reason}
        </span>
      )}
      <span className="flex-1" />
      {badge && (
        <span className="bg-hover text-ink-soft flex-none rounded-full px-2 py-0.5 text-[10px] font-medium">
          {badge}
        </span>
      )}
      {actions}
      {(href || onClick) && !actions && (
        <ChevronRight
          aria-hidden="true"
          className="text-ink-faint size-3.5 flex-none"
        />
      )}
    </>
  )
  const itemClass = cn(
    'band band-hover flex h-9 w-full items-center gap-2.5 px-3 text-left transition-colors',
    className,
  )
  const dataProps = {
    'data-state': selected ? 'selected' : state,
    'data-selected': selected ? 'true' : undefined,
  }
  if (href) {
    return (
      <Link className={itemClass} to={href} {...dataProps}>
        {body}
      </Link>
    )
  }
  return (
    <button
      className={itemClass}
      onClick={onClick}
      type="button"
      {...dataProps}
    >
      {body}
    </button>
  )
}
