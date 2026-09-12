/**
 * Split view: collection beside its detail inspector — the core
 * list + detail layout. The inspector sits on a raised surface.
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
        'border-rule bg-panel grid min-h-0 flex-1 grid-cols-1 overflow-hidden rounded-xl border lg:grid-cols-[minmax(0,1fr)_auto]',
        className,
      )}
    >
      <div className={cn('flex min-w-0 flex-col', listClassName)}>{list}</div>
      <aside
        className={cn(
          'border-rule bg-raised/40 flex min-h-0 flex-col border-t lg:border-t-0 lg:border-l',
          inspectorWidth,
          inspectorClassName,
        )}
      >
        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-4 p-3.5">{inspector}</div>
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
      <h3 className="stamp">{label}</h3>
      {children}
    </section>
  )
}
