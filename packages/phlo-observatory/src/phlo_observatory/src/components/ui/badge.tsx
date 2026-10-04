/** Provides semantic badge variants for compact status labels. */
import * as React from 'react'
import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

/** Status pill. Colour is a signal: red = broken, amber = waiting on a person, green = healthy. */
const badgeVariants = cva(
  'inline-flex w-fit shrink-0 items-center gap-1.5 whitespace-nowrap rounded-[5px] px-2 py-0.5 text-xs font-normal [&_svg]:size-3',
  {
    variants: {
      variant: {
        bad: 'bg-bad-soft text-bad-ink',
        warn: 'bg-warn-soft text-warn-ink',
        ok: 'bg-ok-soft text-ok-ink',
        branch: 'bg-branch-soft text-branch',
        info: 'bg-info-soft text-info',
        neutral: 'bg-soft text-text-2',
        outline: 'border border-border-strong px-[7px] py-px text-text-3',
      },
      size: {
        default: '',
        lg: 'rounded-md text-[12.5px]',
        sm: 'px-1.5 py-0 text-[11px]',
      },
    },
    defaultVariants: { variant: 'neutral', size: 'default' },
  },
)

function Badge({
  className,
  variant,
  size,
  ...props
}: React.ComponentProps<'span'> & VariantProps<typeof badgeVariants>) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ variant, size }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
