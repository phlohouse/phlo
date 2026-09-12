/**
 * Progress bar primitives built on Base UI progress.
 */
import { Progress as ProgressPrimitive } from '@base-ui/react/progress'

import { cn } from '@/lib/utils'

function Progress({
  className,
  children,
  ...props
}: ProgressPrimitive.Root.Props) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      className={cn('relative w-full', className)}
      {...props}
    >
      {children ?? (
        <ProgressPrimitive.Track className="bg-band block h-1.5 w-full overflow-hidden rounded-none">
          <ProgressPrimitive.Indicator className="bg-ink block h-full" />
        </ProgressPrimitive.Track>
      )}
    </ProgressPrimitive.Root>
  )
}

export { Progress }
