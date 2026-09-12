/**
 * Button primitives wrapping the Base UI button, with cva variant and size
 * styling.
 */
import { Button as ButtonPrimitive } from '@base-ui/react/button'
import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  "focus-visible:border-ink focus-visible:ring-ink/30 aria-invalid:ring-print-red/30 aria-invalid:border-print-red rounded-none border border-transparent bg-clip-padding font-mono text-[11px] font-bold tracking-[0.1em] uppercase focus-visible:ring-1 aria-invalid:ring-1 [&_svg:not([class*='size-'])]:size-4 inline-flex items-center justify-center whitespace-nowrap disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none shrink-0 [&_svg]:shrink-0 outline-none group/button select-none",
  {
    variants: {
      variant: {
        default: 'bg-ink text-paper [a]:hover:bg-ink/85',
        outline:
          'border-rule-soft bg-sheet hover:bg-band hover:text-ink aria-expanded:bg-band aria-expanded:text-ink',
        secondary:
          'bg-band text-ink hover:bg-band-strong aria-expanded:bg-band aria-expanded:text-ink',
        ghost:
          'hover:bg-band hover:text-ink aria-expanded:bg-band aria-expanded:text-ink',
        destructive:
          'bg-print-red text-paper hover:bg-print-red/90 focus-visible:ring-print-red/30 focus-visible:border-print-red/50',
        link: 'text-ink underline underline-offset-4 hover:decoration-2',
      },
      size: {
        default:
          'h-8 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2',
        xs: "h-6 gap-1 rounded-none px-2 text-xs has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1 rounded-none px-2.5 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: 'h-9 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-3 has-data-[icon=inline-start]:pl-3',
        icon: 'size-8',
        'icon-xs': "size-6 rounded-none [&_svg:not([class*='size-'])]:size-3",
        'icon-sm': 'size-7 rounded-none',
        'icon-lg': 'size-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
)

function Button({
  className,
  variant = 'default',
  size = 'default',
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
