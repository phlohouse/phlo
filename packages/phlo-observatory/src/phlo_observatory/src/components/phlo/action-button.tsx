/** Applies Phlo availability policy without coupling the presentation button to APIs. */
import * as React from 'react'
import type { ActionContext } from '@/lib/data/api/action-availability'
import { Button } from '@/components/ui/button'
import { useActionPermission } from '@/components/phlo/action-availability'
import { useGroupedActionReason } from '@/components/ui/action-group'

export function ActionButton({
  operation,
  actionContext,
  ...props
}: React.ComponentProps<typeof Button> & {
  operation?: string
  actionContext?: ActionContext
}) {
  const permission = useActionPermission(operation, actionContext)
  const reasonId = React.useId()
  const label =
    props['aria-label'] ??
    React.Children.toArray(props.children)
      .filter((child) => typeof child === 'string')
      .join(' ')
      .trim()
  const grouped = useGroupedActionReason(
    reasonId,
    permission.allowed
      ? undefined
      : {
          label: label || 'Action',
          reason: permission.reason ?? 'This action is unavailable.',
        },
  )
  const button = (
    <Button
      {...props}
      disabled={props.disabled || !permission.allowed}
      aria-describedby={
        [props['aria-describedby'], !permission.allowed ? reasonId : undefined]
          .filter(Boolean)
          .join(' ') || undefined
      }
    />
  )
  if (grouped)
    return (
      <>
        {button}
        {!permission.allowed ? (
          <span id={reasonId} className="sr-only">
            {permission.reason ?? 'This action is unavailable.'}
          </span>
        ) : null}
      </>
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
