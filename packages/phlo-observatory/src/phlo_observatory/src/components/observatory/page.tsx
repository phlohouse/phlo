/**
 * Page scaffolding: a report header (breadcrumb, title, description,
 * actions) over a single column of content, plus the divider used between
 * major sections.
 */
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function Page({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col gap-5 p-4 md:px-6', className)}>
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
    <header className="flex flex-col gap-2 pb-1">
      {breadcrumb && breadcrumb.length > 0 && (
        <nav
          aria-label="Breadcrumb"
          className="text-ink-faint flex items-center gap-1.5 text-xs"
        >
          {breadcrumb.map((item, index) => (
            <span
              className="flex items-center gap-1.5"
              key={`${item.label}-${index}`}
            >
              {index > 0 && <span aria-hidden="true">/</span>}
              {item.to && index < breadcrumb.length - 1 ? (
                <Link className="hover:text-ink" to={item.to}>
                  {item.label}
                </Link>
              ) : (
                <span className="text-ink-soft">{item.label}</span>
              )}
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-ink text-lg leading-tight font-semibold tracking-tight">
            {title}
          </h1>
          {description && (
            <p className="text-ink-soft mt-1 max-w-3xl text-sm/relaxed">
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

/** Section divider; the optional index sits on the rule as a faint chip. */
export function Tear({ index }: { index?: string }) {
  return (
    <div aria-hidden="true" className="tear my-1 flex justify-end">
      {index && (
        <span className="bg-panel border-rule text-ink-faint relative -top-2 rounded-full border px-2 text-[10px]">
          {index}
        </span>
      )}
    </div>
  )
}
