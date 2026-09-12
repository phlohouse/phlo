/**
 * Fact grid for inspector panels: label/value pairs rendered as a compact
 * description list.
 */
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function FactGrid({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <dl
      className={cn(
        'grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-3 gap-y-1.5',
        className,
      )}
    >
      {children}
    </dl>
  )
}

export function Fact({
  label,
  value,
  className,
}: {
  label: ReactNode
  value: ReactNode
  className?: string
}) {
  return (
    <>
      <dt
        className={cn(
          'text-muted-foreground truncate text-[11px] font-medium tracking-wide uppercase',
          className,
        )}
      >
        {label}
      </dt>
      <dd className="text-foreground min-w-0 truncate font-mono text-[11px]">
        {value ?? '—'}
      </dd>
    </>
  )
}
