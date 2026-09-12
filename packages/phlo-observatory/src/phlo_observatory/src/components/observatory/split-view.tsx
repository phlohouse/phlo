/**
 * Split view: a scrollable item list beside an inspector panel — the core
 * mission-control layout for collection + detail pages.
 */
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import { ScrollArea } from '@/components/ui/scroll-area'

export function SplitView({
  list,
  inspector,
  className,
  listClassName,
  inspectorClassName,
  inspectorWidth = 'w-[22rem]',
}: {
  list: ReactNode
  inspector: ReactNode
  className?: string
  listClassName?: string
  inspectorClassName?: string
  inspectorWidth?: string
}) {
  return (
    <div
      className={cn(
        'ring-foreground/10 bg-card grid min-h-0 flex-1 grid-cols-1 overflow-hidden rounded-none ring-1 lg:grid-cols-[minmax(0,1fr)_auto]',
        className,
      )}
    >
      <div className={cn('flex min-w-0 flex-col', listClassName)}>{list}</div>
      <aside
        className={cn(
          'border-t lg:border-t-0 lg:border-l flex min-h-0 flex-col',
          inspectorWidth,
          inspectorClassName,
        )}
      >
        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-4 p-3">{inspector}</div>
        </ScrollArea>
      </aside>
    </div>
  )
}

export function InspectorSection({
  label,
  children,
  className,
}: {
  label: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn('flex flex-col gap-1.5', className)}>
      <h3 className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
        {label}
      </h3>
      {children}
    </section>
  )
}
