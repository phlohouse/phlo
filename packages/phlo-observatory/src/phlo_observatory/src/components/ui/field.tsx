/** Provides labelled form-field and help-text primitives. */
import * as React from 'react'
import { Field as FieldPrimitive } from '@base-ui/react/field'
import { cn } from '@/lib/utils'

/** Label + control + help text, built on Base UI Field so labels and descriptions are wired up for screen readers. */
function Field({
  className,
  ...props
}: React.ComponentProps<typeof FieldPrimitive.Root>) {
  return (
    <FieldPrimitive.Root
      data-slot="field"
      className={cn('flex flex-col gap-1.5', className)}
      {...props}
    />
  )
}

function FieldLabel({
  className,
  optional,
  children,
  ...props
}: React.ComponentProps<typeof FieldPrimitive.Label> & { optional?: boolean }) {
  return (
    <FieldPrimitive.Label
      data-slot="field-label"
      className={cn('text-[13.5px] font-medium text-foreground', className)}
      {...props}
    >
      {children}
      {optional ? (
        <span className="ml-1 font-normal text-muted-foreground">optional</span>
      ) : null}
    </FieldPrimitive.Label>
  )
}

function FieldDescription({
  className,
  ...props
}: React.ComponentProps<typeof FieldPrimitive.Description>) {
  return (
    <FieldPrimitive.Description
      data-slot="field-description"
      className={cn(
        'text-[12.5px] leading-snug text-muted-foreground',
        className,
      )}
      {...props}
    />
  )
}

function Label({ className, ...props }: React.ComponentProps<'label'>) {
  return (
    // reason: This generic primitive's consumer supplies the associated control.
    // eslint-disable-next-line jsx-a11y/label-has-associated-control
    <label
      data-slot="label"
      className={cn('text-[13.5px] font-medium text-foreground', className)}
      {...props}
    />
  )
}

export { Field, FieldLabel, FieldDescription, Label }
