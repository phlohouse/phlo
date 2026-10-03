/** Provides shared empty, loading, not-found, and route-error states. */
import * as React from 'react'
import { Link, useRouter } from '@tanstack/react-router'
import { CircleCheckIcon, SearchXIcon, TriangleAlertIcon } from 'lucide-react'
import type { ErrorComponentProps } from '@tanstack/react-router'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

/** Empty state: say what's fine, and when it was last checked. */
export function EmptyState({
  title,
  children,
  icon,
  action,
  className,
}: {
  title: React.ReactNode
  children?: React.ReactNode
  icon?: React.ReactNode
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center gap-2 rounded-[10px] bg-sunken px-6 py-8 text-center',
        className,
      )}
    >
      <span className="inline-flex size-[22px] items-center justify-center rounded-md bg-soft text-muted-foreground">
        {icon ?? <CircleCheckIcon className="size-3" />}
      </span>
      <div className="text-sm font-medium">{title}</div>
      {children ? (
        <div className="max-w-sm text-[12.5px] text-muted-foreground">
          {children}
        </div>
      ) : null}
      {action}
    </div>
  )
}

/** Skeleton that keeps the page layout while data loads, so nothing jumps. */
export function PageSkeleton() {
  return (
    <div
      className="flex flex-1 flex-col gap-5 p-6"
      aria-busy="true"
      aria-label="Loading"
    >
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="flex flex-col gap-2 rounded-xl border border-border-card p-4"
          >
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-7 w-20" />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-3 rounded-xl border border-border-card p-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton
            key={i}
            className="h-4"
            style={{ width: `${90 - i * 8}%` }}
          />
        ))}
      </div>
    </div>
  )
}

export function NotFound() {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <EmptyState
        title="Nothing here"
        icon={<SearchXIcon className="size-3" />}
        action={<Link to="/">Back to overview</Link>}
      >
        That page, table or incident doesn't exist — or it was renamed.
      </EmptyState>
    </div>
  )
}

export function RouteError({ error, reset }: ErrorComponentProps) {
  const router = useRouter()
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="flex max-w-md flex-col gap-3 rounded-xl border border-bad-line bg-bad-wash p-5 text-bad-ink">
        <div className="flex items-center gap-2 font-medium">
          <TriangleAlertIcon className="size-4" /> Couldn't load this page
        </div>
        <pre className="m-0 overflow-auto font-mono text-xs whitespace-pre-wrap">
          {error instanceof Error ? error.message : String(error)}
        </pre>
        <div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              reset()
              void router.invalidate()
            }}
          >
            Try again
          </Button>
        </div>
      </div>
    </div>
  )
}
