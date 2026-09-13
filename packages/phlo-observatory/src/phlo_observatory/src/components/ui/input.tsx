/**
 * Text input wrapping the Base UI input with the shared field styling.
 */
import * as React from 'react'
import { Input as InputPrimitive } from '@base-ui/react/input'

import { cn } from '@/lib/utils'

function Input({ className, type, ...props }: React.ComponentProps<'input'>) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        'border-rule bg-raised focus-visible:border-blue/60 focus-visible:ring-ring/30 aria-invalid:ring-destructive/30 aria-invalid:border-destructive disabled:bg-hover h-8 rounded-lg border px-2.5 py-1 font-mono text-xs file:h-6 file:text-xs file:font-medium focus-visible:ring-2 aria-invalid:ring-1 md:text-xs file:text-foreground placeholder:text-muted-foreground w-full min-w-0 outline-none file:inline-flex file:border-0 file:bg-transparent disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 transition-colors',
        className,
      )}
      {...props}
    />
  )
}

export { Input }
