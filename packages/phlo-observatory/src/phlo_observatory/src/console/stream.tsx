/**
 * Stream drawer: the bottom band of the console. The merged operations +
 * log event timeline — collapsed it shows the latest event as a ticker,
 * expanded it's a scrollable stream. Clicking an event focuses the object
 * behind it where one is known.
 */
import { ChevronDown, ChevronUp } from 'lucide-react'
import { useMemo } from 'react'

import type { PulseEvent } from '@/components/pulse/pulse-model'
import { formatRelativeTime } from '@/components/observatory/time'
import { cn } from '@/lib/utils'

const DOT: Record<string, string> = {
  error: 'bg-print-red',
  info: 'bg-blue',
  ok: 'bg-ok-ink',
  unknown: 'bg-ink-faint',
  warning: 'bg-amber-ink',
}

function eventFocus(event: PulseEvent): string | null {
  if (event.kind === 'operation') return `op:${event.id.slice(3)}`
  if (event.kind === 'log' && event.id.startsWith('log:')) return null
  return null
}

export function StreamDrawer({
  events,
  open,
  onToggle,
  onFocus,
}: {
  events: Array<PulseEvent>
  open: boolean
  onToggle: () => void
  onFocus: (focus: string) => void
}) {
  const latest = events[0]
  const visible = useMemo(() => events.slice(0, 200), [events])

  return (
    <div className="border-rule bg-panel flex flex-none flex-col border-t">
      <button
        aria-expanded={open}
        className="hover:bg-hover flex h-8 flex-none items-center gap-2 px-3 text-left transition-colors"
        onClick={onToggle}
        type="button"
      >
        <span className="text-ink-soft text-[11px] font-medium tracking-wide uppercase">
          Stream
        </span>
        <span className="text-ink-faint font-mono text-[10px]">
          {events.length} events
        </span>
        {!open && latest && (
          <span className="text-ink-faint min-w-0 flex-1 truncate text-[11px]">
            <span
              className={cn(
                'mr-1.5 inline-block size-1.5 rounded-full align-middle',
                DOT[latest.state] ?? DOT.unknown,
              )}
            />
            {latest.title}
            <span className="text-ink-faint/60">
              {' '}
              · {formatRelativeTime(latest.at)}
            </span>
          </span>
        )}
        <span className="flex-1" />
        {open ? (
          <ChevronDown className="text-ink-faint size-3.5" />
        ) : (
          <ChevronUp className="text-ink-faint size-3.5" />
        )}
      </button>
      {open && (
        <div className="scrollbar-thin h-52 flex-none overflow-y-auto border-t border-rule/60">
          {visible.map((event) => {
            const focus = eventFocus(event)
            return (
              <button
                className="group hover:bg-hover flex w-full items-center gap-3 px-3 py-1.5 text-left transition-colors"
                disabled={!focus}
                key={event.id}
                onClick={() => focus && onFocus(focus)}
                type="button"
              >
                <span
                  className={cn(
                    'size-1.5 flex-none rounded-full',
                    DOT[event.state] ?? DOT.unknown,
                  )}
                />
                <span className="text-ink-faint w-16 flex-none font-mono text-[10px]">
                  {formatRelativeTime(event.at)}
                </span>
                <span className="text-ink-soft group-hover:text-ink min-w-0 flex-1 truncate text-xs">
                  {event.title}
                </span>
                {event.source && (
                  <span className="text-ink-faint hidden flex-none font-mono text-[10px] sm:inline">
                    {event.source}
                  </span>
                )}
                {event.resourceLabel && (
                  <span className="text-ink-faint hidden max-w-40 flex-none truncate font-mono text-[10px] md:inline">
                    {event.resourceLabel}
                  </span>
                )}
                <span className="bg-hover text-ink-faint flex-none rounded px-1.5 py-px font-mono text-[9px] uppercase">
                  {event.kind === 'operation' ? 'run' : 'log'}
                </span>
              </button>
            )
          })}
          {visible.length === 0 && (
            <div className="text-ink-faint py-8 text-center text-xs">
              Stream is quiet.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
