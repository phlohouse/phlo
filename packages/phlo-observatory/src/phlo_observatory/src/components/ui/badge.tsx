/**
 * Badge primitive styled with class-variance-authority variants.
 */
import { mergeProps } from '@base-ui/react/merge-props'
import { useRender } from '@base-ui/react/use-render'
import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'h-5 gap-1 rounded-none border border-transparent px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-[0.12em] uppercase has-data-[icon=inline-end]:pr-1 has-data-[icon=inline-start]:pl-1 [&>svg]:size-3! inline-flex items-center justify-center w-fit whitespace-nowrap shrink-0 [&>svg]:pointer-events-none focus-visible:border-ink focus-visible:ring-ink/30 focus-visible:ring-1 aria-invalid:ring-print-red/30 aria-invalid:border-print-red overflow-hidden group/badge',
  {
    variants: {
      variant: {
        default: 'bg-ink text-paper [a]:hover:bg-ink/85',
        secondary: 'bg-band text-ink [a]:hover:bg-band-strong',
        destructive: 'bg-band-danger text-print-red',
        outline:
          'border-rule-soft text-ink [a]:hover:bg-band [a]:hover:text-ink',
        ghost: 'hover:bg-band hover:text-ink',
        link: 'text-ink underline underline-offset-4 hover:decoration-2',
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
