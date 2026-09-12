/**
 * Breadcrumb primitives: nav list with items, links, separators, and the
 * current-page marker.
 */
import { ChevronRight } from 'lucide-react'
import type { ComponentProps, ReactNode } from 'react'

import { cn } from '@/lib/utils'

function Breadcrumb({ className, ...props }: ComponentProps<'nav'>) {
  return (
    <nav
      aria-label="Breadcrumb"
      data-slot="breadcrumb"
      className={cn(className)}
      {...props}
    />
  )
}

function BreadcrumbList({ className, ...props }: ComponentProps<'ol'>) {
  return (
    <ol
      data-slot="breadcrumb-list"
      className={cn(
        'text-muted-foreground flex flex-wrap items-center gap-1 text-xs',
        className,
      )}
      {...props}
    />
  )
}

function BreadcrumbItem({ className, ...props }: ComponentProps<'li'>) {
  return (
    <li
      data-slot="breadcrumb-item"
      className={cn('inline-flex items-center gap-1', className)}
      {...props}
    />
  )
}

function BreadcrumbLink({ className, ...props }: ComponentProps<'a'>) {
  return (
    <a
      data-slot="breadcrumb-link"
      className={cn('hover:text-foreground transition-colors', className)}
      {...props}
    />
  )
}

function BreadcrumbPage({ className, ...props }: ComponentProps<'span'>) {
  return (
    <span
      aria-current="page"
      data-slot="breadcrumb-page"
      className={cn('text-foreground font-medium', className)}
      {...props}
    />
  )
}

function BreadcrumbSeparator({
  children,
  className,
  ...props
}: ComponentProps<'li'> & { children?: ReactNode }) {
  return (
    <li
      aria-hidden="true"
      data-slot="breadcrumb-separator"
      className={cn('[&>svg]:size-3', className)}
      role="presentation"
      {...props}
    >
      {children ?? <ChevronRight />}
    </li>
  )
}

export {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
}
