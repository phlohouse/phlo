/**
 * The waterline view — the deck's situation display.
 *
 * Dataset lanes on a shared 24h axis. Bars are operations; running work
 * pulses against the now-line, failures print red, publishes tick thin.
 * The freshness marker answers "when did data last land here" even on
 * lanes with no current work. Read the shape: bursts, stalls, gaps.
 */

import { Waves } from 'lucide-react'

import type { WaterlineBar, WaterlineModel } from './waterline-model'
import type { StatusState } from '@/components/observatory/status'
import { HealthDot } from '@/components/observatory/status'
import { formatRelativeTime } from '@/components/observatory/time'
import { cn } from '@/lib/utils'

const TICKS = ['-24h', '-16h', '-8h', 'now']

function barClass(bar: WaterlineBar): string {
  if (bar.publish) return 'bg-link w-[3px] rounded-sm'
  switch (bar.status) {
    case 'running':
      return 'bg-blue animate-pulse rounded-[3px]'
    case 'failed':
      return 'bg-print-red rounded-[3px]'
    case 'queued':
      return 'border border-ink-faint bg-transparent rounded-[3px]'
    default:
      return 'bg-ink-faint/60 rounded-[3px] hover:bg-ink-soft'
  }
}

function barTitle(bar: WaterlineBar): string {
  const duration =
    bar.durationMs >= 3_600_000
      ? `${(bar.durationMs / 3_600_000).toFixed(1)}h`
      : bar.durationMs >= 60_000
        ? `${Math.round(bar.durationMs / 60_000)}m`
        : `${Math.round(bar.durationMs / 1000)}s`
  return `${bar.label} · ${bar.status} · ${duration} · ${formatRelativeTime(bar.startedAt)}`
}

function Lane({
  bars,
  focusTarget,
  label,
  landedAtFrac,
  onFocus,
  state,
}: {
  bars: Array<WaterlineBar>
  focusTarget: string
  label: string
  landedAtFrac?: number
  onFocus: (target: string) => void
  state: StatusState
}) {
  return (
    <div className="border-rule-soft group flex h-7 items-stretch border-b last:border-b-0">
      <button
        className="group-hover:bg-hover flex w-44 flex-none items-center gap-1.5 px-2.5 text-left"
        onClick={() => onFocus(focusTarget)}
        type="button"
      >
        <HealthDot className="status-dot-sm" state={state} />
        <span className="text-ink min-w-0 flex-1 truncate text-[11px] font-medium">
          {label}
        </span>
      </button>
      <div className="group-hover:bg-hover/60 relative min-w-0 flex-1">
        {landedAtFrac !== undefined && (
          <div
            className="bg-ok absolute top-1.5 bottom-1.5 w-px"
            style={{ left: `${landedAtFrac * 100}%` }}
            title="Last landed"
          />
        )}
        {bars.map((bar) => (
          <button
            className={cn('absolute top-2 h-3', barClass(bar))}
            key={bar.id}
            onClick={() => onFocus(bar.focusTarget)}
            style={{
              left: `${bar.left * 100}%`,
              width: bar.publish ? undefined : `${bar.width * 100}%`,
            }}
            title={barTitle(bar)}
            type="button"
          />
        ))}
      </div>
    </div>
  )
}

export function WaterlineView({
  model,
  onFocus,
}: {
  model: WaterlineModel
  onFocus: (target: string) => void
}) {
  const nowFrac =
    (Date.now() - model.windowStart) / (model.windowEnd - model.windowStart)

  return (
    <section className="border-rule bg-panel flex h-full min-h-0 flex-col overflow-hidden rounded-lg border shadow-sm">
      <header className="panel-heading">
        <Waves className="size-3" />
        Waterline
        <span className="text-ink-faint font-mono normal-case tracking-normal">
          last 24h · {model.lanes.length} lanes
          {model.laneOverflow > 0 && ` · +${model.laneOverflow} more`}
        </span>
      </header>

      {/* Axis */}
      <div className="border-rule flex h-6 flex-none items-stretch border-b">
        <div className="w-44 flex-none" />
        <div className="relative min-w-0 flex-1">
          {TICKS.map((tick, i) => (
            <span
              className="text-ink-faint absolute top-1 font-mono text-[9px] -translate-x-1/2"
              key={tick}
              style={{ left: `${(i / (TICKS.length - 1)) * nowFrac * 100}%` }}
            >
              {tick}
            </span>
          ))}
        </div>
      </div>

      {/* Lanes */}
      <div className="scrollbar-thin relative min-h-0 flex-1 overflow-y-auto">
        {/* Gridlines + now-line span the lane area. */}
        <div className="pointer-events-none absolute inset-y-0 left-44 right-0">
          {[1 / 3, 2 / 3].map((pos) => (
            <div
              className="bg-rule-soft absolute inset-y-0 w-px"
              key={pos}
              style={{ left: `${pos * nowFrac * 100}%` }}
            />
          ))}
          <div
            className="bg-blue/60 absolute inset-y-0 w-px"
            style={{ left: `${nowFrac * 100}%` }}
          />
        </div>

        {model.lanes.length === 0 && model.platformBars.length === 0 ? (
          <div className="text-ink-faint flex h-full flex-col items-center justify-center gap-1.5">
            <Waves className="size-4" />
            <span className="text-xs">Flat water</span>
            <span className="max-w-64 text-center text-[10px]">
              No operations touched the lake in the last 24 hours.
            </span>
          </div>
        ) : (
          <>
            {model.lanes.map((lane) => (
              <Lane
                bars={lane.bars}
                focusTarget={lane.focusTarget}
                key={lane.id}
                label={lane.label}
                landedAtFrac={lane.landedAtFrac}
                onFocus={onFocus}
                state={lane.state}
              />
            ))}
            {model.platformBars.length > 0 && (
              <Lane
                bars={model.platformBars}
                focusTarget=""
                label="platform"
                onFocus={onFocus}
                state="unknown"
              />
            )}
          </>
        )}
      </div>

      {/* Legend */}
      <footer className="border-rule text-ink-faint flex h-6 flex-none items-center gap-3 border-t px-3 text-[9px]">
        <span className="flex items-center gap-1">
          <span className="bg-blue inline-block h-2 w-3 animate-pulse rounded-[2px]" />
          running
        </span>
        <span className="flex items-center gap-1">
          <span className="bg-ink-faint/60 inline-block h-2 w-3 rounded-[2px]" />
          ran
        </span>
        <span className="flex items-center gap-1">
          <span className="bg-print-red inline-block h-2 w-3 rounded-[2px]" />
          failed
        </span>
        <span className="flex items-center gap-1">
          <span className="bg-link inline-block h-2.5 w-[3px] rounded-sm" />
          published
        </span>
        <span className="flex items-center gap-1">
          <span className="bg-ok inline-block h-2.5 w-px" />
          landed
        </span>
      </footer>
    </section>
  )
}
