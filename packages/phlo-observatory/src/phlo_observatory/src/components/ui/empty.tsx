/**
 * Empty-state block for panels and lists with no content.
 */
import type { ComponentProps, ReactNode } from 'react'

import { cn } from '@/lib/utils'

function Empty({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="empty"
      className={cn(
        'border-rule mx-auto flex max-w-lg flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-8 text-center',
        className,
      )}
      {...props}
    />
  )
}

function EmptyIcon({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="empty-icon"
      className={cn(
        'text-ink-faint bg-raised border-rule [&_svg]:size-4 flex size-8 items-center justify-center rounded-lg border',
        className,
      )}
      {...props}
    />
  )
}

function EmptyTitle({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="empty-title"
      className={cn('text-ink text-sm font-medium', className)}
      {...props}
    />
  )
}

function EmptyDescription({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="empty-description"
      className={cn(
        'text-muted-foreground max-w-sm text-xs/relaxed',
        className,
      )}
      {...props}
    />
  )
}

function EmptyActions({
  className,
  ...props
}: ComponentProps<'div'> & { children?: ReactNode }) {
  return (
    <div
      data-slot="empty-actions"
      className={cn('mt-2 flex items-center gap-2', className)}
      {...props}
    />
  )
}

export { Empty, EmptyActions, EmptyDescription, EmptyIcon, EmptyTitle }
