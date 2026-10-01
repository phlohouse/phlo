/** Provides timeline layouts for events and comments. */
import * as React from 'react'
import { toneDot } from './status'
import type { Tone } from '@/lib/data/types'
import { cn } from '@/lib/utils'
import { Avatar } from '@/components/ui/avatar'

/** Vertical activity timeline with a connecting line. Dot colour = kind of event. */
export function Timeline({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return (
    <ol
      className={cn(
        'relative m-0 flex list-none flex-col gap-3.5 p-0',
        className,
      )}
    >
      <span
        aria-hidden
        className="absolute top-2 bottom-2 left-[5px] w-px bg-line"
      />
      {children}
    </ol>
  )
}

export function TimelineItem({
  tone = 'neutral',
  ring,
  icon,
  meta,
  children,
  compact,
}: {
  tone?: Tone
  /** Hollow ring instead of a filled dot (status changes). */
  ring?: boolean
  icon?: React.ReactNode
  meta?: React.ReactNode
  children?: React.ReactNode
  /** Single line: meta only, vertically centred. */
  compact?: boolean
}) {
  return (
    <li className={cn('relative flex gap-3.5', compact && 'items-center')}>
      <span
        className={cn(
          'relative flex w-[11px] shrink-0 justify-center',
          !compact && 'pt-[5px]',
        )}
      >
        {icon ? (
          <span className="bg-card">{icon}</span>
        ) : ring ? (
          <span className="size-[11px] rounded-full border-2 border-warn bg-card" />
        ) : (
          <span
            className={cn(
              'box-content size-[9px] rounded-full border-2 border-card',
              toneDot[tone],
            )}
          />
        )}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {meta ? (
          <div className="text-[13px] text-muted-foreground">{meta}</div>
        ) : null}
        {children ? (
          <div className="text-[14.5px] leading-normal">{children}</div>
        ) : null}
      </div>
    </li>
  )
}

/** A comment card in the timeline. */
export function TimelineComment({
  initials,
  name,
  when,
  tone = 'dark',
  children,
}: {
  initials: string
  name: string
  when: string
  tone?: 'dark' | 'teal'
  children: React.ReactNode
}) {
  return (
    <li className="relative flex flex-col gap-2 rounded-[10px] border border-border-card bg-card px-4 py-3.5">
      <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
        <Avatar initials={initials} tone={tone} />
        <span>
          <span className="text-text-2">{name}</span> commented · {when}
        </span>
      </div>
      <div className="text-[14.5px] leading-normal">{children}</div>
    </li>
  )
}
