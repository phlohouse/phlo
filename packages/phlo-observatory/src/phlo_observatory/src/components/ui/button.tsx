/** Provides styled button variants backed by Base UI. */
import * as React from 'react'
import { Button as ButtonPrimitive } from '@base-ui/react/button'
import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'
import type { ActionContext } from '@/lib/data/api/action-availability'
import { cn } from '@/lib/utils'
import { useActionPermission } from '@/components/phlo/action-availability'

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg text-[13.5px] font-medium transition-colors outline-none select-none cursor-pointer disabled:pointer-events-none disabled:opacity-45 data-[disabled]:pointer-events-none data-[disabled]:opacity-45 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
  {
    variants: {
      variant: {
        default:
          'bg-primary text-primary-foreground hover:bg-primary/90 hover:text-primary-foreground',
        outline:
          'border border-border bg-raised font-normal text-text-2 hover:bg-soft hover:text-foreground',
        secondary:
          'bg-soft font-normal text-text-2 hover:bg-nav-on hover:text-foreground',
        ghost: 'font-normal text-text-3 hover:bg-soft hover:text-foreground',
        destructive:
          'border border-bad-line bg-raised font-normal text-bad-text hover:bg-bad-wash hover:text-bad-text',
        link: 'h-auto px-0 font-normal text-link underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-8 px-3',
        sm: 'h-7 px-2.5 text-[13px]',
        lg: 'h-9 px-4 text-sm',
        icon: 'size-8',
        'icon-sm': 'size-7',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

type ButtonProps = React.ComponentProps<typeof ButtonPrimitive> &
  VariantProps<typeof buttonVariants> & {
    operation?: string
    actionContext?: ActionContext
  }

function Button({
  className,
  variant,
  size,
  operation,
  actionContext,
  ...props
}: ButtonProps) {
  const permission = useActionPermission(operation, actionContext)
  const reasonId = React.useId()
  const button = (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
      disabled={props.disabled || !permission.allowed}
      aria-describedby={
        !permission.allowed ? reasonId : props['aria-describedby']
      }
    />
  )
  return permission.allowed ? (
    button
  ) : (
    <span className="inline-flex min-w-0 max-w-full flex-col items-start gap-1">
      {button}
      <span
        id={reasonId}
        className="max-w-56 whitespace-normal text-xs font-normal text-muted-foreground"
      >
        {permission.reason ?? 'This action is unavailable.'}
      </span>
    </span>
  )
}

export { Button, buttonVariants }
