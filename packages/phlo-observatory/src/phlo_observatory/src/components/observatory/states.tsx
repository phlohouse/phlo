/**
 * Shared loading / empty / error states plus a boundary that renders the
 * right one for an ObservatoryResourceResult.
 */
import { AlertTriangle, Inbox } from 'lucide-react'
import type { ReactNode } from 'react'

import type { ObservatoryResourceResult } from '@/observatory/api/types'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Empty,
  EmptyDescription,
  EmptyIcon,
  EmptyTitle,
} from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'

export function LoadingBlock({
  label = 'Loading',
  rows = 4,
  className,
}: {
  label?: string
  rows?: number
  className?: string
}) {
  return (
    <div aria-label={label} className={cn('bands flex flex-col', className)}>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton className="h-7 w-full" key={index} />
      ))}
    </div>
  )
}

export function EmptyBlock({
  title,
  description,
  action,
  className,
}: {
  title: string
  description?: string
  action?: ReactNode
  className?: string
}) {
  return (
    <Empty className={className}>
      <EmptyIcon>
        <Inbox />
      </EmptyIcon>
      <EmptyTitle>{title}</EmptyTitle>
      {description && <EmptyDescription>{description}</EmptyDescription>}
      {action}
    </Empty>
  )
}

export function ErrorBlock({
  title = 'Lakehouse API unavailable',
  error,
  onRetry,
  className,
}: {
  title?: string
  error: ReactNode
  onRetry?: () => void
  className?: string
}) {
  return (
    <Empty className={className}>
      <EmptyIcon className="text-print-red">
        <AlertTriangle />
      </EmptyIcon>
      <EmptyTitle>{title}</EmptyTitle>
      <EmptyDescription className="font-mono break-all">
        {error}
      </EmptyDescription>
      {onRetry && (
        <Button onClick={onRetry} size="sm" variant="outline">
          Retry
        </Button>
      )}
    </Empty>
  )
}

/**
 * Renders children with the loaded data, or the matching state block.
 * `isEmpty` customizes what counts as an empty payload (defaults to
 * empty arrays / nullish data).
 */
export function ResourceBoundary<T>({
  result,
  isLoading,
  isEmpty,
  loading,
  empty,
  children,
  onRetry,
  className,
}: {
  result: ObservatoryResourceResult<T>
  isLoading?: boolean
  isEmpty?: (data: T) => boolean
  loading?: ReactNode
  empty?: ReactNode
  children: (data: T) => ReactNode
  onRetry?: () => void
  className?: string
}) {
  const emptyCheck =
    isEmpty ?? ((data: T) => Array.isArray(data) && data.length === 0)
  if (result.data !== null && !emptyCheck(result.data)) {
    return <>{children(result.data)}</>
  }
  if (result.error && result.data === null) {
    return (
      <ErrorBlock
        className={className}
        error={result.error}
        onRetry={onRetry}
      />
    )
  }
  if (isLoading || (result.data === null && !result.error)) {
    return <div className={className}>{loading ?? <LoadingBlock />}</div>
  }
  return (
    <div className={className}>
      {empty ?? <EmptyBlock title="Nothing here yet" />}
    </div>
  )
}
