/** Provides checkbox, radio, choice-card, and switch controls. */
import * as React from 'react'
import { Checkbox as CheckboxPrimitive } from '@base-ui/react/checkbox'
import { Radio as RadioPrimitive } from '@base-ui/react/radio'
import { RadioGroup as RadioGroupPrimitive } from '@base-ui/react/radio-group'
import { Switch as SwitchPrimitive } from '@base-ui/react/switch'
import { CheckIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

function Checkbox({
  className,
  ...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        'peer inline-flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-[4px] border border-border-strong bg-card outline-none',
        'data-[checked]:border-primary data-[checked]:bg-primary data-[checked]:text-primary-foreground',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center">
        <CheckIcon className="size-3" strokeWidth={3} />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
}

/** Checkbox with its label, as one click target. */
function CheckLine({
  children,
  className,
  ...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root> & {
  children: React.ReactNode
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-2.5 text-sm leading-snug text-foreground',
        className,
      )}
    >
      <Checkbox className="mt-0.5" {...props} />
      <span>{children}</span>
    </label>
  )
}

function RadioGroup({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive>) {
  return (
    <RadioGroupPrimitive
      data-slot="radio-group"
      className={cn('flex flex-wrap gap-2', className)}
      {...props}
    />
  )
}

function RadioDot({
  className,
  ...props
}: React.ComponentProps<typeof RadioPrimitive.Root>) {
  return (
    <RadioPrimitive.Root
      data-slot="radio"
      className={cn(
        'inline-flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-full border border-border-strong bg-card outline-none',
        'data-[checked]:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
        className,
      )}
      {...props}
    >
      <RadioPrimitive.Indicator className="size-2 rounded-full bg-primary" />
    </RadioPrimitive.Root>
  )
}

/** "Choice" chip: radio + label in a bordered pill. */
function ChoiceItem({
  value,
  children,
  className,
}: {
  value: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <label
      className={cn(
        'flex h-[34px] cursor-pointer items-center gap-2 rounded-lg border border-border bg-card px-3 text-[13.5px] text-text-2',
        'has-[[data-checked]]:border-primary has-[[data-checked]]:bg-primary-soft has-[[data-checked]]:text-foreground',
        'has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring',
        className,
      )}
    >
      <RadioDot value={value} />
      {children}
    </label>
  )
}

/** "Option card": radio with a bold title and a hint line, for decisions. */
function OptionCard({
  value,
  title,
  hint,
  className,
}: {
  value: string
  title: React.ReactNode
  hint?: React.ReactNode
  className?: string
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-3 rounded-[10px] border border-border px-3.5 py-3',
        'has-[[data-checked]]:border-primary has-[[data-checked]]:bg-primary-soft',
        'has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring',
        className,
      )}
    >
      <RadioDot value={value} className="mt-0.5" />
      <span className="flex flex-col gap-0.5">
        <span className="text-sm font-medium text-foreground">{title}</span>
        {hint ? (
          <span className="text-[12.5px] leading-snug text-muted-foreground">
            {hint}
          </span>
        ) : null}
      </span>
    </label>
  )
}

function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full bg-border-strong p-0.5 outline-none transition-colors data-[checked]:bg-primary',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="size-4 rounded-full bg-white shadow transition-transform data-[checked]:translate-x-4" />
    </SwitchPrimitive.Root>
  )
}

export {
  Checkbox,
  CheckLine,
  RadioGroup,
  RadioDot,
  ChoiceItem,
  OptionCard,
  Switch,
}
