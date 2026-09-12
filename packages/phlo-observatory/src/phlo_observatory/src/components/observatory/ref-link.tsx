/**
 * Resource reference links: resolve a phlo-api ResourceRef (kind + id) to the
 * Observatory surface that inspects it, preserving deep-link params.
 */
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import type { ObservatoryResourceRef } from '@/observatory/api/types'
import { cn } from '@/lib/utils'

export function resourceRefHref(ref: ObservatoryResourceRef): string | null {
  const id = encodeURIComponent(ref.id)
  switch (ref.kind) {
    case 'service':
      return `/services?serviceId=${id}`
    case 'dataset':
      return `/datasets/${id}`
    case 'table':
      return `/tables?tableId=${id}`
    case 'asset':
      return `/lineage?assetId=${id}`
    case 'run':
      return `/runs?runId=${id}`
    case 'operation':
      return `/operations?operationId=${id}`
    case 'quality':
    case 'check':
      return `/quality?checkId=${id}`
    case 'branch':
      return `/branches?branchId=${id}`
    case 'log':
      return `/logs?logId=${id}`
    case 'pipeline':
      return `/pipelines?pipelineId=${id}`
    case 'extension':
      return `/extensions/${id}`
    case 'api':
      return `/apis?apiId=${id}`
    case 'surface':
      return `/bi?surfaceId=${id}`
    case 'storage':
      return `/storage?providerId=${id}`
    case 'observability':
      return `/observability?providerId=${id}`
    default:
      return null
  }
}

export function ResourceRefLink({
  ref,
  children,
  className,
}: {
  ref: ObservatoryResourceRef
  children?: ReactNode
  className?: string
}) {
  const href = resourceRefHref(ref)
  const label = children ?? ref.label ?? ref.id
  if (!href) {
    return (
      <span
        className={cn('text-muted-foreground font-mono text-[11px]', className)}
      >
        {label}
      </span>
    )
  }
  return (
    <Link
      className={cn(
        'text-foreground hover:text-primary font-mono text-[11px] underline-offset-4 hover:underline',
        className,
      )}
      to={href}
    >
      {label}
    </Link>
  )
}
