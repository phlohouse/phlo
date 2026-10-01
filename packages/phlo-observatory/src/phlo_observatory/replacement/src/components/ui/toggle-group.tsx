/** Provides toggle-group and segmented-choice controls. */
import * as React from 'react'
import { ToggleGroup as ToggleGroupPrimitive } from '@base-ui/react/toggle-group'
import { Toggle as TogglePrimitive } from '@base-ui/react/toggle'
import { cn } from '@/lib/utils'

/** Segmented control (the "seg" from the design): 24 h / 7 d / 30 d, List / Timeline… */
function ToggleGroup({
  className,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive>) {
  return (
    <ToggleGroupPrimitive
      data-slot="toggle-group"
      className={cn(
        'inline-flex rounded-lg border border-border bg-raised p-0.5',
        className,
      )}
      {...props}
    />
  )
}

function ToggleGroupItem({
  className,
  ...props
}: React.ComponentProps<typeof TogglePrimitive>) {
  return (
    <TogglePrimitive
      data-slot="toggle-group-item"
      className={cn(
        // 36px touch target on phones, compact 26px on desktop
        'h-9 cursor-pointer rounded-md px-3 text-[13px] text-text-3 outline-none lg:h-[26px] lg:px-2.5',
        'data-[pressed]:bg-card data-[pressed]:text-foreground data-[pressed]:shadow-[0_0_0_1px_var(--border)]',
        'focus-visible:outline-2 focus-visible:outline-ring',
        className,
      )}
      {...props}
    />
  )
}

/** A single-choice segmented control with a plain string value. */
function Segmented<T extends string>({
  value,
  onValueChange,
  options,
  className,
  'aria-label': ariaLabel,
}: {
  value: T
  onValueChange: (v: T) => void
  options: ReadonlyArray<{ value: T; label: React.ReactNode }>
  className?: string
  'aria-label'?: string
}) {
  return (
    <ToggleGroup
      aria-label={ariaLabel}
      className={className}
      value={[value]}
      onValueChange={(next) => {
        const v = next[0] as T | undefined
        if (v) onValueChange(v)
      }}
    >
      {options.map((o) => (
        <ToggleGroupItem key={o.value} value={o.value}>
          {o.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

export { ToggleGroup, ToggleGroupItem, Segmented }
