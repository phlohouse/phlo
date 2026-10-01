/** Maps domain statuses to consistent labels, tones, badges, and tiles. */
import * as React from 'react'
import {
  CircleCheckIcon,
  ClockIcon,
  GitMergeIcon,
  Grid2X2Icon,
  TrendingUpIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react'
import type {
  DayCode,
  IncidentKind,
  IncidentStatus,
  LayerOrCatalog,
  RunCode,
  Severity,
  Tone,
} from '@/lib/data/types'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'

/* ---------- Tone helpers ---------- */
export const toneDot: Record<Tone, string> = {
  bad: 'bg-bad',
  warn: 'bg-warn',
  ok: 'bg-ok',
  branch: 'bg-branch',
  info: 'bg-primary',
  neutral: 'bg-faint',
}

export const toneText: Record<Tone, string> = {
  bad: 'text-bad-text',
  warn: 'text-warn-ink',
  ok: 'text-ok-text',
  branch: 'text-branch',
  info: 'text-info',
  neutral: 'text-muted-foreground',
}

export function Dot({
  tone,
  size = 'sm',
  className,
}: {
  tone: Tone
  size?: 'sm' | 'md'
  className?: string
}) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-block shrink-0 rounded-full',
        size === 'sm' ? 'size-[7px]' : 'size-2',
        toneDot[tone],
        className,
      )}
    />
  )
}

/* ---------- Severity + status ---------- */
const severityTone: Record<Severity, Tone> = {
  high: 'bad',
  medium: 'warn',
  low: 'neutral',
}
const severityLabel: Record<Severity, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export function SeverityBadge({
  severity,
  size,
}: {
  severity: Severity
  size?: 'default' | 'lg' | 'sm'
}) {
  return (
    <Badge variant={severityTone[severity]} size={size}>
      {severityLabel[severity]}
    </Badge>
  )
}

const statusMeta: Record<
  IncidentStatus,
  { label: string; tone: Tone; ring?: boolean }
> = {
  investigating: { label: 'Investigating', tone: 'warn', ring: true },
  'in-progress': { label: 'In progress', tone: 'info' },
  triage: { label: 'Triage', tone: 'neutral', ring: true },
  blocked: { label: 'Blocked', tone: 'bad' },
  monitoring: { label: 'Monitoring', tone: 'info' },
  resolved: { label: 'Resolved', tone: 'ok' },
}

export function statusLabel(s: IncidentStatus) {
  return statusMeta[s].label
}

/** "● Investigating" — a small dot/ring and the status word. */
export function IncidentStatusText({
  status,
  className,
}: {
  status: IncidentStatus
  className?: string
}) {
  const m = statusMeta[status]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-[13px] text-text-2',
        className,
      )}
    >
      {m.ring ? (
        <span
          className={cn(
            'size-2 shrink-0 rounded-full border-2',
            m.tone === 'warn' ? 'border-warn' : 'border-faint',
          )}
        />
      ) : (
        <Dot tone={m.tone} />
      )}
      {m.label}
    </span>
  )
}

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  const m = statusMeta[status]
  return (
    <Badge variant={m.tone === 'neutral' ? 'neutral' : m.tone} size="lg">
      {m.ring ? (
        <span className="size-2 rounded-full border-2 border-current" />
      ) : (
        <Dot tone={m.tone} />
      )}
      {m.label}
    </Badge>
  )
}

/* ---------- Incident kind tile ---------- */
const kindTile: Record<
  IncidentKind,
  {
    cls: string
    Icon: React.ComponentType<{ className?: string; strokeWidth?: number }>
  }
> = {
  freshness: { cls: 'bg-teal-soft text-teal', Icon: ClockIcon },
  schema: { cls: 'bg-warn-soft text-warn-ink', Icon: Grid2X2Icon },
  audit: { cls: 'bg-bad-soft text-bad-text', Icon: XIcon },
  catalog: { cls: 'bg-branch-soft text-branch', Icon: GitMergeIcon },
  performance: { cls: 'bg-info-soft text-info', Icon: TrendingUpIcon },
  maintenance: { cls: 'bg-soft text-muted-foreground', Icon: WrenchIcon },
}

export function IncidentTile({
  kind,
  resolved,
  size = 'sm',
  className,
}: {
  kind: IncidentKind
  resolved?: boolean
  size?: 'sm' | 'lg'
  className?: string
}) {
  const { cls, Icon } = resolved
    ? { cls: 'bg-soft text-muted-foreground', Icon: CircleCheckIcon }
    : kindTile[kind]
  return (
    <span
      aria-hidden
      className={cn(
        'inline-flex shrink-0 items-center justify-center',
        size === 'sm' ? 'size-[18px] rounded-[5px]' : 'size-[22px] rounded-md',
        cls,
        className,
      )}
    >
      <Icon
        className={size === 'sm' ? 'size-[11px]' : 'size-3'}
        strokeWidth={2.2}
      />
    </span>
  )
}

/* ---------- Layers ---------- */
const layerSwatch: Record<LayerOrCatalog, string> = {
  bronze: 'bg-bronze',
  silver: 'bg-silver',
  gold: 'bg-gold',
  catalog: 'bg-branch',
}

export function LayerSwatch({
  layer,
  size = 'sm',
}: {
  layer: LayerOrCatalog
  size?: 'sm' | 'lg'
}) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-block shrink-0',
        size === 'sm' ? 'size-2 rounded-[2px]' : 'size-2.5 rounded-[3px]',
        layerSwatch[layer],
      )}
    />
  )
}

export function LayerLabel({
  layer,
  className,
}: {
  layer: LayerOrCatalog
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-[13.5px] text-text-2',
        className,
      )}
    >
      <LayerSwatch layer={layer} />
      {layer[0].toUpperCase() + layer.slice(1)}
    </span>
  )
}

/* ---------- Bars and strips ---------- */
/** Green share vs red share, e.g. 141 fresh / 7 stale. */
export function HealthBar({
  ok,
  bad,
  className,
}: {
  ok: number
  bad: number
  className?: string
}) {
  return (
    <div
      className={cn('flex h-[5px] gap-0.5', className)}
      role="img"
      aria-label={`${ok} healthy, ${bad} not`}
    >
      <div className="rounded-[2px] bg-ok" style={{ flexGrow: ok }} />
      {bad > 0 ? (
        <div className="rounded-[2px] bg-bad" style={{ flexGrow: bad }} />
      ) : null}
    </div>
  )
}

const runCell: Record<RunCode, string> = {
  s: 'bg-ok-bar',
  w: 'bg-warn-bar',
  f: 'bg-bad',
  k: 'border border-skip-line',
  p: 'bg-soft',
  n: '',
}
const runWord: Record<RunCode, string> = {
  s: 'succeeded',
  w: 'slow',
  f: 'failed',
  k: 'skipped',
  p: 'paused',
  n: 'no run',
}

/** One cell per run, newest on the right. */
export function RunStrip({
  runs,
  size = 'md',
  className,
}: {
  runs: Array<RunCode>
  size?: 'sm' | 'md'
  className?: string
}) {
  const failed = runs.filter((r) => r === 'f').length
  return (
    <div
      className={cn(
        'flex items-center',
        size === 'md' ? 'gap-0.5' : 'gap-px',
        className,
      )}
      role="img"
      aria-label={`Last ${runs.length} runs: ${failed} failed`}
    >
      {runs.map((r, i) => (
        <span
          key={i}
          title={`Run ${i + 1}: ${runWord[r]}`}
          className={cn(
            'box-border shrink-0 rounded-[2px]',
            size === 'md' ? 'h-[22px] w-2' : 'h-3.5 w-[5px] rounded-[1px]',
            runCell[r],
          )}
        />
      ))}
    </div>
  )
}

export function RunLegend({ className }: { className?: string }) {
  const items: Array<[string, string]> = [
    ['bg-ok-bar', 'Succeeded'],
    ['bg-warn-bar', 'Slow'],
    ['bg-bad', 'Failed'],
    ['border border-skip-line', 'Skipped'],
  ]
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-4 text-[13px] text-muted-foreground',
        className,
      )}
    >
      {items.map(([c, l]) => (
        <span key={l} className="inline-flex items-center gap-1.5">
          <span className={cn('box-border h-3 w-2 rounded-[2px]', c)} />
          {l}
        </span>
      ))}
    </div>
  )
}

const dayCell: Record<DayCode, string> = {
  g: 'bg-sla-ok',
  a: 'bg-sla-late',
  r: 'bg-bad',
}

/** Last 7 days of SLA: met / late / breached. */
export function DayStrip({
  days,
  className,
}: {
  days: Array<DayCode>
  className?: string
}) {
  return (
    <div
      className={cn('flex gap-[3px]', className)}
      role="img"
      aria-label="Freshness, last 7 days"
    >
      {days.map((d, i) => (
        <span key={i} className={cn('h-4 w-3 rounded-[3px]', dayCell[d])} />
      ))}
    </div>
  )
}

/* ---------- Text ---------- */
export function Mono({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <span className={cn('font-mono text-[0.92em]', className)}>{children}</span>
  )
}

/** Renders `backtick` spans as monospace — used for activity text that names tables and columns. */
export function RichText({
  text,
  className,
}: {
  text: string
  className?: string
}) {
  const parts = text.split('`')
  return (
    <span className={className}>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <Mono key={i}>{p}</Mono>
        ) : (
          <React.Fragment key={i}>{p}</React.Fragment>
        ),
      )}
    </span>
  )
}
