/**
 * Section: a form block on the sheet — stamped header over a ruled body.
 * Not a card; the boundary is ink, not a box.
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
        <header className="border-rule flex flex-wrap items-center justify-between gap-2 border-b pb-1">
          <div className="min-w-0">
            {title && <h2 className="stamp text-ink text-[11px]">{title}</h2>}
            {description && (
              <p className="text-ink-soft mt-0.5 font-mono text-[10px]/relaxed normal-case">
                {description}
              </p>
            )}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn('min-w-0 flex-1 pt-2', contentClassName)}>
        {children}
      </div>
    </section>
  )
}
