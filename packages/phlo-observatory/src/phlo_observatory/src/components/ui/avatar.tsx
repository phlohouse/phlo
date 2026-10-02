/** Provides the styled user avatar primitive. */
import * as React from 'react'
import { Avatar as AvatarPrimitive } from '@base-ui/react/avatar'
import { cn } from '@/lib/utils'

function Avatar({
  initials,
  tone = 'dark',
  size = 'sm',
  className,
  ...props
}: React.ComponentProps<typeof AvatarPrimitive.Root> & {
  initials: string
  tone?: 'dark' | 'teal'
  size?: 'sm' | 'md' | 'lg'
}) {
  return (
    <AvatarPrimitive.Root
      data-slot="avatar"
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full font-semibold select-none',
        size === 'sm' && 'size-5 text-[9px]',
        size === 'md' && 'size-[26px] text-[10px]',
        size === 'lg' && 'size-[26px] border-2 border-card text-[11px]',
        tone === 'dark'
          ? 'bg-foreground text-background'
          : 'bg-teal-soft text-teal-ink',
        className,
      )}
      {...props}
    >
      <AvatarPrimitive.Fallback>{initials}</AvatarPrimitive.Fallback>
    </AvatarPrimitive.Root>
  )
}

export { Avatar }
