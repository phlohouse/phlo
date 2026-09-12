/**
 * Textarea element with the shared field styling and content-based sizing.
 */
import * as React from 'react'

import { cn } from '@/lib/utils'

function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        'border-rule bg-raised focus-visible:border-blue/60 focus-visible:ring-ring/30 aria-invalid:ring-destructive/30 aria-invalid:border-destructive disabled:bg-hover rounded-lg border px-2.5 py-2 font-mono text-xs transition-colors focus-visible:ring-2 aria-invalid:ring-1 md:text-xs placeholder:text-muted-foreground flex field-sizing-content min-h-16 w-full outline-none disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}

export { Textarea }
