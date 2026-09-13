/**
 * Badge primitive styled with class-variance-authority variants.
 */
import { mergeProps } from '@base-ui/react/merge-props'
import { useRender } from '@base-ui/react/use-render'
import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'h-5 gap-1 rounded-full border border-transparent px-2 py-0.5 text-[11px] font-medium has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&>svg]:size-3! inline-flex items-center justify-center w-fit whitespace-nowrap shrink-0 [&>svg]:pointer-events-none focus-visible:ring-ring/50 focus-visible:ring-2 aria-invalid:ring-destructive/30 aria-invalid:border-destructive overflow-hidden group/badge',
  {
    variants: {
      variant: {
        default: 'bg-selected text-ink',
        secondary: 'bg-hover text-ink-soft [a]:hover:bg-selected',
        destructive: 'bg-destructive/15 text-destructive',
        outline: 'border-rule text-ink-soft [a]:hover:bg-hover',
        ghost: 'hover:bg-hover hover:text-ink',
        link: 'text-link underline-offset-4 hover:underline',
        ok: 'bg-status-ok/15 text-status-ok',
        warning: 'bg-status-warning/15 text-status-warning',
        info: 'bg-status-info/15 text-status-info',
        violet: 'bg-violet/15 text-violet',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

function Badge({
  className,
  variant = 'default',
  render,
  ...props
}: useRender.ComponentProps<'span'> & VariantProps<typeof badgeVariants>) {
  return useRender({
    defaultTagName: 'span',
    props: mergeProps<'span'>(
      {
        className: cn(badgeVariants({ className, variant })),
      },
      props,
    ),
    render,
    state: {
      slot: 'badge',
      variant,
    },
  })
}

export { Badge }
