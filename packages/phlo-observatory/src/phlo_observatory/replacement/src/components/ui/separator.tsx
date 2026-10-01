/** Provides separator, loading skeleton, and keyboard-key primitives. */
import * as React from 'react'
import { Separator as SeparatorPrimitive } from '@base-ui/react/separator'
import { cn } from '@/lib/utils'

function Separator({
  className,
  orientation = 'horizontal',
  ...props
}: React.ComponentProps<typeof SeparatorPrimitive>) {
  return (
    <SeparatorPrimitive
      data-slot="separator"
      orientation={orientation}
      className={cn(
        'shrink-0 bg-line',
        orientation === 'horizontal' ? 'h-px w-full' : 'h-full w-px',
        className,
      )}
      {...props}
    />
  )
}

function Skeleton({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="skeleton"
      className={cn('animate-pulse rounded-[5px] bg-soft', className)}
      {...props}
    />
  )
}

function Kbd({ className, ...props }: React.ComponentProps<'kbd'>) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        'rounded border border-border bg-card px-1.5 py-px font-mono text-[11px] text-muted-foreground',
        className,
      )}
      {...props}
    />
  )
}

export { Separator, Skeleton, Kbd }
