/**
 * Section card: titled panel used to group related blocks on a page.
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
    <section
      className={cn(
        'bg-card ring-foreground/10 flex flex-col rounded-none ring-1',
        className,
      )}
    >
      {(title || actions || description) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
          <div className="min-w-0">
            {title && (
              <h2 className="text-foreground text-xs font-semibold tracking-wide">
                {title}
              </h2>
            )}
            {description && (
              <p className="text-muted-foreground mt-0.5 text-[11px]/relaxed">
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
