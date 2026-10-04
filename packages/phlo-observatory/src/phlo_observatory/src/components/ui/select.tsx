/** Provides an accessible typed single-value select control. */
import * as React from 'react'
import { Select as SelectPrimitive } from '@base-ui/react/select'
import { CheckIcon, ChevronDownIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

type Option<T extends string> = { value: T; label: React.ReactNode }

/** A simple single-value select on Base UI. */
function Select<T extends string>({
  value,
  onValueChange,
  options,
  className,
  id,
  disabled,
  'aria-label': ariaLabel,
}: {
  value: T
  onValueChange: (v: T) => void
  options: ReadonlyArray<Option<T>>
  className?: string
  id?: string
  disabled?: boolean
  'aria-label'?: string
}) {
  return (
    <SelectPrimitive.Root
      value={value}
      disabled={disabled}
      onValueChange={(v) => {
        if (v != null) onValueChange(v)
      }}
      items={options.map((o) => ({ value: o.value, label: o.label }))}
    >
      <SelectPrimitive.Trigger
        id={id}
        aria-label={ariaLabel}
        className={cn(
          'flex h-9 w-full cursor-pointer items-center gap-2 rounded-lg border border-input bg-card px-3 text-left text-sm text-foreground outline-none',
          'focus-visible:border-primary focus-visible:ring-3 focus-visible:ring-primary-soft',
          className,
        )}
      >
        <SelectPrimitive.Value className="min-w-0 flex-1 truncate" />
        <SelectPrimitive.Icon className="text-muted-foreground">
          <ChevronDownIcon className="size-3.5" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Positioner
          sideOffset={6}
          className="z-50"
          alignItemWithTrigger={false}
        >
          <SelectPrimitive.Popup className="min-w-[var(--anchor-width)] rounded-xl border border-border-card bg-popover p-1.5 shadow-dialog outline-none">
            <SelectPrimitive.List>
              {options.map((o) => (
                <SelectPrimitive.Item
                  key={o.value}
                  value={o.value}
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13.5px] outline-none select-none data-[highlighted]:bg-soft"
                >
                  <SelectPrimitive.ItemText className="flex-1">
                    {o.label}
                  </SelectPrimitive.ItemText>
                  <SelectPrimitive.ItemIndicator>
                    <CheckIcon className="size-3.5 text-primary" />
                  </SelectPrimitive.ItemIndicator>
                </SelectPrimitive.Item>
              ))}
            </SelectPrimitive.List>
          </SelectPrimitive.Popup>
        </SelectPrimitive.Positioner>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  )
}

export { Select }
