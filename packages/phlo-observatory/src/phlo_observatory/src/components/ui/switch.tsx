/**
 * Switch primitive built on the Base UI switch.
 */
import { Switch as SwitchPrimitive } from '@base-ui/react/switch'

import { cn } from '@/lib/utils'

function Switch({ className, ...props }: SwitchPrimitive.Root.Props) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'data-checked:bg-blue data-unchecked:bg-hover focus-visible:ring-ring/50 peer inline-flex h-4.5 w-8 shrink-0 items-center rounded-full border border-transparent outline-none transition-colors focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="bg-foreground data-checked:bg-white data-checked:translate-x-3.5 data-unchecked:translate-x-0 pointer-events-none block size-3.5 rounded-full transition-transform"
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
