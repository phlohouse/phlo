/** Provides pipeline status indicators, health summaries, and view controls. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { z } from 'zod'
import type { Env, ScaleStatus } from '@/lib/data/types'
import type { ApiRun } from '@/lib/data/api/pipelines'
import { cn } from '@/lib/utils'

export const pipelineSearchSchema = z.object({
  q: z.string().default(''),
  by: z.enum(['domain', 'owner', 'source']).default('domain'),
  owners: z.array(z.string()).default([]),
  sources: z.array(z.string()).default([]),
  states: z
    .array(z.enum(['failed', 'succeeded', 'other', 'unknown']))
    .default(['failed', 'succeeded', 'other', 'unknown']),
  saved: z.array(z.enum(['mine', 'release'])).default([]),
})

export function runColor(status: ApiRun['status']) {
  return status === 'FAILURE'
    ? 'bg-bad'
    : status === 'SUCCESS'
      ? 'bg-sla-ok'
      : status === 'CANCELED'
        ? 'bg-skip-line'
        : 'bg-warn-bar'
}

export function runBadge(status: ApiRun['status']) {
  return status === 'FAILURE'
    ? 'bad'
    : status === 'SUCCESS'
      ? 'ok'
      : status === 'CANCELED'
        ? 'neutral'
        : 'warn'
}

export function runDuration(seconds: number | null) {
  if (seconds === null) return 'Not available'
  if (seconds < 60) return `${seconds.toFixed(2)} seconds`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

export const statusDot: Record<ScaleStatus, string> = {
  failing: 'bg-bad',
  slow: 'bg-warn-bar',
  paused: 'bg-skip-line',
  ok: 'bg-ok',
}

export const statusText: Record<ScaleStatus, string> = {
  failing: 'text-bad-text',
  slow: 'text-warn-ink',
  paused: 'text-muted-foreground',
  ok: 'text-text-2',
}

export function StatusDot({
  status,
  className,
}: {
  status: ScaleStatus
  className?: string
}) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-block size-2 shrink-0 rounded-full',
        statusDot[status],
        className,
      )}
    />
  )
}

/** Proportional bar: failing · slow · paused · healthy. */
export function HealthMix({
  parts,
  className,
  label,
}: {
  parts: Array<{ n: number; cls: string }>
  className?: string
  label?: string
}) {
  return (
    <span
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn('flex h-2 gap-px overflow-hidden rounded-[3px]', className)}
    >
      {parts
        .filter((p) => p.n > 0)
        .map((p, i) => (
          <span key={i} className={p.cls} style={{ flexGrow: p.n }} />
        ))}
    </span>
  )
}

/** "List | Timeline" switch. Real links, styled like the segmented control. */
export function ViewSwitch({
  current,
  env,
}: {
  current: 'list' | 'timeline'
  env: Env
}) {
  const item =
    'flex h-[26px] items-center rounded-md px-2.5 text-[13px] text-text-3 hover:text-foreground'
  const on = 'bg-card text-foreground shadow-[0_0_0_1px_var(--border)]'
  return (
    <nav
      aria-label="Pipelines view"
      className="inline-flex rounded-lg border border-border bg-raised p-0.5"
    >
      <Link
        to="/pipelines"
        search={(previous) => ({ ...previous, env })}
        aria-current={current === 'list' ? 'page' : undefined}
        className={cn(item, current === 'list' && on)}
      >
        List
      </Link>
      <Link
        to="/pipelines/timeline"
        search={(previous) => ({
          ...previous,
          env,
          range: previous.range === '7d' ? '7d' : '24h',
        })}
        aria-current={current === 'timeline' ? 'page' : undefined}
        className={cn(item, current === 'timeline' && on)}
      >
        Timeline
      </Link>
    </nav>
  )
}

/** Correlated-failures icon (three linked nodes). */
export function LinkedIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('size-3.5 shrink-0', className)}
    >
      <circle cx="4" cy="8" r="2" />
      <circle cx="12" cy="3.5" r="2" />
      <circle cx="12" cy="12.5" r="2" />
      <path d="M6 7l4-2.5" />
      <path d="M6 9l4 2.5" />
    </svg>
  )
}

export function Facet({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <fieldset className="m-0 flex flex-col gap-0.5 border-0 p-0">
      <legend className="px-1.5 pb-1.5 text-[11.5px] tracking-[0.06em] text-muted-foreground uppercase">
        {title}
      </legend>
      {children}
    </fieldset>
  )
}
