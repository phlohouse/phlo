/** Displays compact KPI cards and labelled statistics. */
import * as React from 'react'
import { Eyebrow } from './page'
import { cn } from '@/lib/utils'

/** KPI card: eyebrow, big number with a short qualifier, and a line or bar under it. */
export function KpiCard({
  label,
  value,
  qualifier,
  qualifierTone,
  footer,
  className,
  ...props
}: {
  label: React.ReactNode
  value: React.ReactNode
  qualifier?: React.ReactNode
  qualifierTone?: 'bad' | 'muted'
  footer?: React.ReactNode
} & Omit<React.ComponentProps<'div'>, 'children'>) {
  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-xl border border-border-card bg-card px-4 py-3.5 lg:px-[18px] lg:py-4',
        className,
      )}
      {...props}
    >
      <Eyebrow>{label}</Eyebrow>
      <div className="flex flex-wrap items-baseline gap-x-1.5">
        <span className="text-[26px] leading-tight font-medium tracking-tight lg:text-[28px]">
          {value}
        </span>
        {qualifier ? (
          <span
            className={cn(
              'text-[13px]',
              qualifierTone === 'bad'
                ? 'text-bad-text'
                : 'text-muted-foreground',
            )}
          >
            {qualifier}
          </span>
        ) : null}
      </div>
      {footer ? (
        <div className="text-[13px] text-muted-foreground">{footer}</div>
      ) : null}
    </div>
  )
}

/** Small bordered stat used on detail pages. */
export function Stat({
  label,
  value,
  sub,
  tone,
  className,
}: {
  label: React.ReactNode
  value: React.ReactNode
  sub?: React.ReactNode
  tone?: 'bad' | 'warn' | 'ok'
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col gap-1 rounded-[10px] border border-line px-3.5 py-3',
        className,
      )}
    >
      <Eyebrow>{label}</Eyebrow>
      <span
        className={cn(
          'text-[17px] font-medium',
          tone === 'bad' && 'text-bad-text',
          tone === 'warn' && 'text-warn-ink',
          tone === 'ok' && 'text-ok-text',
        )}
      >
        {value}
      </span>
      {sub ? (
        <span className="text-xs text-muted-foreground">{sub}</span>
      ) : null}
    </div>
  )
}
