/**
 * Page scaffolding: consistent header (kicker, title, description, actions)
 * and content width for every Observatory surface.
 */
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb'

export function Page({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col gap-4 p-4 md:p-5', className)}>
      {children}
    </div>
  )
}

export function PageHeader({
  breadcrumb,
  title,
  description,
  actions,
  children,
}: {
  breadcrumb?: Array<{ label: string; to?: string }>
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="flex flex-col gap-3">
      {breadcrumb && breadcrumb.length > 0 && (
        <Breadcrumb>
          <BreadcrumbList>
            {breadcrumb.map((item, index) => (
              <BreadcrumbItem key={`${item.label}-${index}`}>
                {index > 0 && <BreadcrumbSeparator />}
                {item.to && index < breadcrumb.length - 1 ? (
                  <Link
                    className="hover:text-foreground transition-colors"
                    to={item.to}
                  >
                    {item.label}
                  </Link>
                ) : (
                  <BreadcrumbPage>{item.label}</BreadcrumbPage>
                )}
              </BreadcrumbItem>
            ))}
          </BreadcrumbList>
        </Breadcrumb>
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-foreground text-lg font-semibold tracking-tight">
            {title}
          </h1>
          {description && (
            <p className="text-muted-foreground mt-0.5 max-w-3xl text-xs/relaxed">
              {description}
            </p>
          )}
        </div>
        {actions && (
          <div className="flex flex-none items-center gap-2">{actions}</div>
        )}
      </div>
      {children}
    </header>
  )
}
