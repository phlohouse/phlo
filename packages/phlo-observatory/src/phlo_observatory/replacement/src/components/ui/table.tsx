/** Provides consistently styled semantic table primitives. */
import * as React from 'react'
import { cn } from '@/lib/utils'

function Table({ className, ...props }: React.ComponentProps<'table'>) {
  return (
    <table
      data-slot="table"
      className={cn(
        'w-full caption-bottom border-collapse text-[13.5px]',
        className,
      )}
      {...props}
    />
  )
}
function TableHeader({ className, ...props }: React.ComponentProps<'thead'>) {
  return (
    <thead
      data-slot="table-header"
      className={cn('bg-raised', className)}
      {...props}
    />
  )
}
function TableBody({ className, ...props }: React.ComponentProps<'tbody'>) {
  return <tbody data-slot="table-body" className={className} {...props} />
}
function TableRow({ className, ...props }: React.ComponentProps<'tr'>) {
  return (
    <tr
      data-slot="table-row"
      className={cn('border-b border-line-soft hover:bg-raised', className)}
      {...props}
    />
  )
}
function TableHead({ className, ...props }: React.ComponentProps<'th'>) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        'h-9 px-3 text-left align-middle text-xs font-normal tracking-wide whitespace-nowrap text-muted-foreground first:pl-5 last:pr-5',
        className,
      )}
      {...props}
    />
  )
}
function TableCell({ className, ...props }: React.ComponentProps<'td'>) {
  return (
    <td
      data-slot="table-cell"
      className={cn('h-11 px-3 align-middle first:pl-5 last:pr-5', className)}
      {...props}
    />
  )
}

export { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }
