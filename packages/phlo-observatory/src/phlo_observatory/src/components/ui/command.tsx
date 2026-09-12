/**
 * Command palette primitives wrapping cmdk with the shared surface styling.
 */
import { Command as CommandPrimitive } from 'cmdk'
import { SearchIcon } from 'lucide-react'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

function Command({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive>) {
  return (
    <CommandPrimitive
      data-slot="command"
      className={cn(
        'bg-popover text-popover-foreground flex h-full w-full flex-col overflow-hidden',
        className,
      )}
      {...props}
    />
  )
}

function CommandInput({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <div
      className="flex h-10 items-center gap-2 border-b px-3"
      data-slot="command-input-wrapper"
    >
      <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          'placeholder:text-muted-foreground flex h-10 w-full bg-transparent text-sm outline-none disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        {...props}
      />
    </div>
  )
}

function CommandList({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      data-slot="command-list"
      className={cn(
        'scrollbar-thin max-h-[min(24rem,var(--cmdk-list-height,24rem))] overflow-y-auto overflow-x-hidden',
        className,
      )}
      {...props}
    />
  )
}

function CommandEmpty({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      data-slot="command-empty"
      className={cn(
        'text-muted-foreground py-6 text-center text-xs',
        className,
      )}
      {...props}
    />
  )
}

function CommandGroup({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      className={cn(
        'text-foreground **:[[cmdk-group-heading]]:text-muted-foreground overflow-hidden **:[[cmdk-group-heading]]:px-2.5 **:[[cmdk-group-heading]]:py-1.5 **:[[cmdk-group-heading]]:text-[10px] **:[[cmdk-group-heading]]:font-medium **:[[cmdk-group-heading]]:tracking-widest **:[[cmdk-group-heading]]:uppercase',
        className,
      )}
      {...props}
    />
  )
}

function CommandItem({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        "data-[selected=true]:bg-ink data-[selected=true]:text-paper [&_svg:not([class*='size-'])]:size-3.5 relative flex cursor-default items-center gap-2 px-2.5 py-1.5 font-mono text-[11px] outline-none select-none data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg]:text-muted-foreground data-[selected=true]:[&_svg]:text-paper",
        className,
      )}
      {...props}
    />
  )
}

function CommandShortcut({ className, ...props }: ComponentProps<'span'>) {
  return (
    <span
      data-slot="command-shortcut"
      className={cn(
        'text-muted-foreground ml-auto font-mono text-[10px] tracking-widest',
        className,
      )}
      {...props}
    />
  )
}

export {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
}
