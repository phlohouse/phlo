/**
 * Banded row list: the printout's core unit. Rows sit on green-bar bands;
 * a row's `state` tints its band, selection inverts to ink. Fixed single-
 * line height, no wrapping — the sheet is dense.
 */
import { Link } from '@tanstack/react-router'
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
    <div className={cn('bands border-rule border-t border-b', className)}>
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
      <span className="truncate font-mono text-[11px] font-bold">{title}</span>
      {meta && (
        <span className="text-ink-soft hidden truncate font-mono text-[10px] md:inline">
          {meta}
        </span>
      )}
      {reason && (
        <span className="text-ink-faint hidden min-w-0 flex-1 truncate font-mono text-[10px] lg:inline">
          {reason}
        </span>
      )}
      <span className="flex-1" />
      {badge && (
        <span className="text-ink-faint flex-none font-mono text-[9px] tracking-[0.12em] uppercase">
          {badge}
        </span>
      )}
      {actions}
      {(href || onClick) && !actions && (
        <span
          aria-hidden="true"
          className="text-ink-faint font-mono text-[10px]"
        >
          &gt;
        </span>
      )}
    </>
  )
  const itemClass = cn(
    'band band-hover flex h-7 w-full items-center gap-2.5 px-3 text-left',
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
