/** Provides asset-specific status, SLA, and inline-editing controls. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import type { AssetHealth, AssetTag, Tone } from '@/lib/data/types'
import { Badge } from '@/components/ui/badge'
import { Mono } from '@/components/phlo/status'
import { cn } from '@/lib/utils'

export const healthTone: Record<AssetHealth, Tone> = {
  stale: 'bad',
  warn: 'warn',
  ok: 'ok',
}

/** "#214" / "compaction due" pill; links to the incident when there is one. */
export function AssetTagPill({
  tag,
  link = true,
  className,
}: {
  tag: AssetTag
  link?: boolean
  className?: string
}) {
  const cls = cn('px-[7px] py-px text-[11.5px]', className)
  if (link && tag.incidentId) {
    return (
      <Link
        to="/incidents/$incidentId"
        params={{ incidentId: tag.incidentId }}
        aria-label={`Incident ${tag.label}`}
        className="shrink-0 rounded-[5px] hover:opacity-85"
      >
        <Badge variant={tag.tone} className={cls}>
          {tag.label}
        </Badge>
      </Link>
    )
  }
  return (
    <Badge variant={tag.tone} className={cls}>
      {tag.label}
    </Badge>
  )
}

/** Met SLA / Late / Breached legend for the 7-day squares. */
export function SlaLegend({
  prefix,
  className,
}: {
  prefix?: string
  className?: string
}) {
  const items: Array<[string, string]> = [
    ['bg-sla-ok', 'Met SLA'],
    ['bg-sla-late', 'Late'],
    ['bg-bad', 'Breached'],
  ]
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-[13px] text-muted-foreground',
        className,
      )}
    >
      {prefix ? <span>{prefix}</span> : null}
      {items.map(([c, l]) => (
        <span key={l} className="inline-flex items-center gap-1.5">
          <span className={cn('size-2.5 rounded-[2px]', c)} aria-hidden />
          {l}
        </span>
      ))}
    </div>
  )
}

/** Inline text with `mono` spans and **bold** spans. */
export function InlineText({
  text,
  className,
}: {
  text: string
  className?: string
}) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
  return (
    <span className={className}>
      {parts.map((p, i) =>
        p.startsWith('`') ? (
          <Mono key={i}>{p.slice(1, -1)}</Mono>
        ) : p.startsWith('**') ? (
          <strong key={i} className="font-semibold">
            {p.slice(2, -2)}
          </strong>
        ) : (
          <React.Fragment key={i}>{p}</React.Fragment>
        ),
      )}
    </span>
  )
}
