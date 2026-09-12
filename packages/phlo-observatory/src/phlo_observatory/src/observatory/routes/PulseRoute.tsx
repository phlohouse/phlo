/**
 * /pulse — the live event stream. Operations and log events merged into
 * one descending timeline: a marker rail on the left, event rows on the
 * right. Kind and level filters narrow the stream; each row links into
 * its owning workbench. Polls like every other live surface.
 */
import { Link } from '@tanstack/react-router'
import { Pause, Play } from 'lucide-react'
import { useEffect, useMemo, useReducer, useRef, useState } from 'react'

import type {
  ObservatoryLogEvent,
  ObservatoryOperation,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import type { PulseEvent, PulseKind } from '@/components/pulse/pulse-model'
import {
  getObservatoryLogRecords,
  getObservatoryOperationRecords,
} from '@/observatory/api/resources'
import { loadCachedResource } from '@/observatory/routes/liveResource'
import { buildPulseStream } from '@/components/pulse/pulse-model'
import { Page, PageHeader } from '@/components/observatory/page'
import { StatusBadge } from '@/components/observatory/status'
import { EmptyBlock } from '@/components/observatory/states'
import {
  formatDateTime,
  formatRelativeTime,
} from '@/components/observatory/time'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type PulseState = {
  logs: ObservatoryResourceResult<Array<ObservatoryLogEvent>>
  operations: ObservatoryResourceResult<Array<ObservatoryOperation>>
}

const KIND_FILTERS: Array<{ id: PulseKind | 'all'; label: string }> = [
  { id: 'all', label: 'Everything' },
  { id: 'operation', label: 'Runs' },
  { id: 'log', label: 'Logs' },
]

const LEVEL_FILTERS = [
  { id: 'all', label: 'All levels' },
  { id: 'error', label: 'Errors' },
  { id: 'warning', label: 'Warnings' },
] as const

export type PulseLevelFilter = (typeof LEVEL_FILTERS)[number]['id']

const STATE_MARKER: Record<string, string> = {
  error: 'bg-print-red',
  info: 'bg-blue',
  ok: 'bg-ok-ink',
  unknown: 'bg-ink-faint',
  warning: 'bg-amber-ink',
}

export function PulseRoute({
  kind,
  level,
  onKind,
  onLevel,
}: {
  kind: PulseKind | 'all'
  level: PulseLevelFilter
  onKind: (kind: PulseKind | 'all') => void
  onLevel: (level: PulseLevelFilter) => void
}) {
  const [state, setState] = useReducer(
    (current: PulseState, patch: Partial<PulseState>) => ({
      ...current,
      ...patch,
    }),
    {
      logs: { data: null, error: null },
      operations: { data: null, error: null },
    },
  )
  const [live, setLive] = useState(true)
  const knownIds = useRef<Set<string>>(new Set())
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    function load(force = false) {
      for (const [field, loader] of [
        ['operations', getObservatoryOperationRecords],
        ['logs', getObservatoryLogRecords],
      ] as Array<
        [keyof PulseState, () => Promise<ObservatoryResourceResult<unknown>>]
      >) {
        void loadCachedResource(`observatory:${field}`, loader, {
          force,
          staleMs: 15_000,
        }).then((next) => {
          if (!cancelled) setState({ [field]: next } as Partial<PulseState>)
        })
      }
    }
    load(true)
    if (!live) return
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') load(true)
    }, 15_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [live])

  const stream = useMemo(
    () =>
      buildPulseStream({
        operations: state.operations.data ?? [],
        logs: state.logs.data ?? [],
      }),
    [state],
  )

  // Mark events that arrived since first render so they surface fresh.
  useEffect(() => {
    if (!stream.length) return
    const current = new Set(stream.map((event) => event.id))
    if (knownIds.current.size === 0) {
      knownIds.current = current
      return
    }
    const arrived = [...current].filter((id) => !knownIds.current.has(id))
    knownIds.current = current
    if (arrived.length) {
      setFreshIds(new Set(arrived))
      const timeout = window.setTimeout(() => setFreshIds(new Set()), 6_000)
      return () => window.clearTimeout(timeout)
    }
  }, [stream])

  const visible = stream.filter(
    (event) =>
      (kind === 'all' || event.kind === kind) &&
      (level === 'all' ||
        (level === 'error' && event.state === 'error') ||
        (level === 'warning' &&
          (event.state === 'warning' || event.state === 'error'))),
  )
  const loading = !state.operations.data && !state.logs.data

  return (
    <Page className="max-w-4xl">
      <PageHeader
        actions={
          <div className="flex items-center gap-2">
            <StatusBadge
              label={live ? 'Live' : 'Paused'}
              state={live ? 'ok' : 'unknown'}
            />
            <Button
              onClick={() => setLive((next) => !next)}
              size="sm"
              variant="outline"
            >
              {live ? (
                <Pause className="size-3.5" />
              ) : (
                <Play className="size-3.5" />
              )}
              {live ? 'Pause' : 'Resume'}
            </Button>
          </div>
        }
        description="Operations and platform events as they happen."
        title="Pulse"
      />

      <div className="flex flex-wrap items-center gap-1.5">
        {KIND_FILTERS.map(({ id, label }) => (
          <button
            className={cn(
              'flex h-7 items-center rounded-full border px-3 text-xs font-medium transition-colors',
              kind === id
                ? 'border-blue/50 bg-blue-soft text-blue'
                : 'border-rule text-ink-soft hover:border-foreground/25 hover:text-ink',
            )}
            key={id}
            onClick={() => onKind(id)}
            type="button"
          >
            {label}
          </button>
        ))}
        <span className="bg-rule mx-1 h-4 w-px" />
        {LEVEL_FILTERS.map(({ id, label }) => (
          <button
            className={cn(
              'flex h-7 items-center rounded-full border px-3 text-xs font-medium transition-colors',
              level === id
                ? 'border-blue/50 bg-blue-soft text-blue'
                : 'border-rule text-ink-soft hover:border-foreground/25 hover:text-ink',
            )}
            key={id}
            onClick={() => onLevel(id)}
            type="button"
          >
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-ink-faint py-16 text-center text-xs">
          Tuning into the stream…
        </div>
      ) : visible.length === 0 ? (
        <EmptyBlock
          description="No events match the current filters."
          title="Stream is quiet"
        />
      ) : (
        <ol className="relative flex flex-col">
          <span
            aria-hidden="true"
            className="bg-rule absolute top-2 bottom-2 left-[7px] w-px"
          />
          {visible.map((event) => (
            <PulseRow
              event={event}
              fresh={freshIds.has(event.id)}
              key={event.id}
            />
          ))}
        </ol>
      )}
    </Page>
  )
}

function PulseRow({ event, fresh }: { event: PulseEvent; fresh: boolean }) {
  return (
    <li className="relative flex gap-3 py-1.5 pl-0">
      <span
        className={cn(
          'relative z-10 mt-1.5 size-[7px] flex-none rounded-full ring-4 ring-canvas',
          STATE_MARKER[event.state] ?? STATE_MARKER.unknown,
          fresh && 'animate-pulse',
        )}
      />
      <Link
        className={cn(
          'group border-rule hover:border-foreground/25 min-w-0 flex-1 rounded-xl border px-3 py-2 transition-colors',
          fresh ? 'bg-raised' : 'bg-panel',
        )}
        title={formatDateTime(event.at)}
        to={event.href}
      >
        <div className="flex items-baseline gap-2">
          <span className="text-ink-faint w-14 flex-none font-mono text-[10px]">
            {formatRelativeTime(event.at)}
          </span>
          <span className="text-ink group-hover:text-blue min-w-0 flex-1 truncate text-[13px]">
            {event.title}
          </span>
          {event.level && (
            <span className="bg-hover text-ink-soft flex-none rounded-full px-1.5 py-px font-mono text-[10px]">
              {event.level}
            </span>
          )}
        </div>
        {(event.detail || event.source || event.resourceLabel) && (
          <div className="text-ink-faint mt-0.5 flex gap-2 pl-16 font-mono text-[10px]">
            {event.kind === 'operation' && <span>run</span>}
            {event.source && <span>{event.source}</span>}
            {event.detail && <span>{event.detail}</span>}
            {event.resourceLabel && (
              <span className="truncate">scope: {event.resourceLabel}</span>
            )}
          </div>
        )}
      </Link>
    </li>
  )
}
