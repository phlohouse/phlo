/**
 * Section: a titled block — a quiet heading over its content. Content
 * panels (RowList, SplitView, StatGrid) carry their own rounded chrome.
 */
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function SectionCard({
  title,
  description,
  actions,
  children,
  className,
  contentClassName,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  contentClassName?: string
}) {
  return (
    <section className={cn('flex flex-col', className)}>
      {(title || actions || description) && (
        <header className="flex flex-wrap items-center justify-between gap-2 pb-2">
          <div className="min-w-0">
            {title && (
              <h2 className="text-ink text-sm font-semibold tracking-tight">
                {title}
              </h2>
            )}
            {description && (
              <p className="text-ink-soft mt-0.5 text-xs/relaxed">
                {description}
              </p>
            )}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn('min-w-0 flex-1', contentClassName)}>{children}</div>
    </section>
  )
}
