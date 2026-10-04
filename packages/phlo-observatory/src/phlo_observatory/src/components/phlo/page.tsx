/** Provides page headers, bodies, metadata, and key-value layouts. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { ChevronRightIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * The 52px bar at the top of the main panel. On phones it wraps: title first, actions below.
 * Usage: <PageHeader crumbs={[{ label: 'Incidents', to: '/incidents' }]} title="#214 …" meta="…" actions={…} />
 */
export function PageHeader({
  title,
  meta,
  crumbs,
  actions,
  className,
}: {
  title: React.ReactNode
  meta?: React.ReactNode
  crumbs?: Array<{ label: string; to: string }>
  actions?: React.ReactNode
  className?: string
}) {
  return (
    <header
      className={cn(
        'flex min-h-[52px] shrink-0 flex-wrap items-center gap-x-3.5 gap-y-2 border-b border-line px-4 py-2.5 lg:flex-nowrap lg:py-0 lg:pl-5',
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        {crumbs?.map((c) => (
          <React.Fragment key={c.to}>
            <Link
              to={c.to}
              search={(previous) => previous}
              className="text-sm whitespace-nowrap text-muted-foreground hover:text-foreground"
            >
              {c.label}
            </Link>
            <ChevronRightIcon
              className="size-3 shrink-0 text-faint"
              aria-hidden
            />
          </React.Fragment>
        ))}
        <h1 className="m-0 truncate text-sm font-medium">{title}</h1>
      </div>
      {meta ? (
        <span className="hidden truncate text-[13px] text-muted-foreground md:inline">
          {meta}
        </span>
      ) : null}
      {actions ? (
        <div className="ml-auto flex flex-wrap items-center gap-2.5">
          {actions}
        </div>
      ) : null}
    </header>
  )
}

/** Scrolling body of a page. */
export function PageBody({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      className={cn(
        'flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-4 lg:p-6',
        className,
      )}
      {...props}
    />
  )
}

export function Eyebrow({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      className={cn(
        'text-[11.5px] tracking-[0.06em] text-muted-foreground uppercase',
        className,
      )}
      {...props}
    />
  )
}

export function Meta({ className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span
      className={cn('text-[13px] text-muted-foreground', className)}
      {...props}
    />
  )
}

/** Key/value list: 110px keys, values fill. */
export function KeyValues({
  items,
  keyWidth = 110,
  className,
}: {
  items: Array<[React.ReactNode, React.ReactNode]>
  keyWidth?: number
  className?: string
}) {
  return (
    <dl
      className={cn('m-0 grid gap-y-2.5 text-sm', className)}
      style={{ gridTemplateColumns: `${keyWidth}px minmax(0,1fr)` }}
    >
      {items.map(([k, v], i) => (
        <React.Fragment key={i}>
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="m-0 flex min-w-0 items-center gap-2 text-foreground">
            {v}
          </dd>
        </React.Fragment>
      ))}
    </dl>
  )
}
