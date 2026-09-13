/**
 * The deck — Observatory's single surface. One live view of the lakehouse:
 * the lake is the app, and everything else (signals, stream, inspector,
 * palette) is a layer over it, not a page.
 *
 *   ┌ top bar: health · stack · palette · refresh ────────────┐
 *   │ signals │      lake (inbound + datasets)│ inspector(over)│
 *   ├ stream drawer (collapsed = ticker, open = timeline) ────┤
 */
import { RefreshCw } from 'lucide-react'
import { useMemo } from 'react'

import { AttentionRail } from './attention'
import { Inspector } from './inspector'
import { ConsolePalette, PaletteButton, usePalette } from './search'
import { useLakehouseSnapshot } from './snapshot'
import { StreamDrawer } from './stream'
import { parseFocus } from './store'
import type { FocusRef } from './store'
import { buildLakeModel } from '@/components/lake/lake-model'
import { LakeView } from '@/components/lake/lake-view'
import { buildTriageQueue } from '@/components/now/triage-model'
import { buildPulseStream } from '@/components/pulse/pulse-model'
import { HealthDot } from '@/components/observatory/status'
import { formatRelativeTime } from '@/components/observatory/time'
import { ErrorBlock } from '@/components/observatory/states'
import { Button } from '@/components/ui/button'
import { ExtensionSlot } from '@/extensions/registry'
import { cn } from '@/lib/utils'

export function Console({
  demoCount,
  focus,
  streamOpen,
  onFocus,
  onStreamOpen,
}: {
  demoCount?: number | null
  focus: FocusRef | null
  streamOpen: boolean
  onFocus: (focus: FocusRef | null) => void
  onStreamOpen: (open: boolean) => void
}) {
  const { refresh, snapshot } = useLakehouseSnapshot(demoCount)
  const palette = usePalette()

  const lake = useMemo(
    () =>
      buildLakeModel({
        assets: snapshot.assets.data ?? [],
        datasets: snapshot.datasets.data ?? [],
        focus,
        operations: snapshot.operations.data ?? [],
        pipelines: snapshot.pipelines.data ?? [],
        quality: snapshot.quality.data ?? [],
      }),
    [
      focus,
      snapshot.assets.data,
      snapshot.datasets.data,
      snapshot.operations.data,
      snapshot.pipelines.data,
      snapshot.quality.data,
    ],
  )

  const signals = useMemo(
    () =>
      buildTriageQueue({
        operations: snapshot.operations.data ?? [],
        quality: snapshot.quality.data ?? [],
        pipelines: snapshot.pipelines.data ?? [],
        services: snapshot.services.data ?? [],
        logs: snapshot.logs.data ?? [],
      }),
    [
      snapshot.operations.data,
      snapshot.quality.data,
      snapshot.pipelines.data,
      snapshot.services.data,
      snapshot.logs.data,
    ],
  )

  const stream = useMemo(
    () =>
      buildPulseStream({
        operations: snapshot.operations.data ?? [],
        logs: snapshot.logs.data ?? [],
      }),
    [snapshot.operations.data, snapshot.logs.data],
  )

  const focusFromString = (raw: string) => {
    const parsed = parseFocus(raw)
    if (parsed) onFocus(parsed)
  }

  const health = snapshot.overview.data?.health
  const services = snapshot.services.data ?? []
  const booted = snapshot.updatedAt !== null
  const totalFailure =
    booted &&
    !snapshot.assets.data &&
    !snapshot.overview.data &&
    snapshot.overview.error

  return (
    <div className="bg-canvas flex h-svh flex-col overflow-hidden">
      <header className="border-rule bg-panel flex h-11 flex-none items-center gap-3 border-b px-3">
        <div className="flex flex-none items-center gap-2">
          <HealthDot state={health?.state ?? 'unknown'} />
          <span className="text-ink text-sm font-semibold tracking-tight">
            Observatory
          </span>
        </div>

        <div className="border-rule mx-1 hidden h-4 w-px border-l md:block" />

        <div className="scrollbar-thin hidden min-w-0 flex-1 items-center gap-1 overflow-x-auto md:flex">
          {services.map((service) => (
            <button
              className={cn(
                'hover:bg-hover flex flex-none items-center gap-1.5 rounded-full px-2 py-1 transition-colors',
                service.health.state === 'error' && 'bg-status-band-error',
                service.health.state === 'warning' && 'bg-status-band-warning',
              )}
              key={service.id}
              onClick={() => onFocus({ kind: 'service', id: service.id })}
              title={`${service.name} · ${service.status}`}
              type="button"
            >
              <HealthDot state={service.health.state} />
              <span className="text-ink-soft text-[11px] font-medium">
                {service.name}
              </span>
            </button>
          ))}
        </div>
        <div className="flex-1 md:hidden" />

        <ExtensionSlot slotId="observatory.topbar" />

        <PaletteButton onClick={() => palette.setOpen(true)} />

        <div className="text-ink-faint hidden w-16 text-right font-mono text-[10px] sm:block">
          {snapshot.updatedAt
            ? formatRelativeTime(snapshot.updatedAt.toISOString())
            : ''}
        </div>
        <Button
          aria-label="Refresh"
          onClick={refresh}
          size="icon-xs"
          variant="ghost"
        >
          <RefreshCw className="size-3.5" />
        </Button>
      </header>

      <div className="relative flex min-h-0 flex-1">
        <aside className="border-rule bg-panel hidden w-56 flex-none border-r sm:block">
          <AttentionRail items={signals} onFocus={focusFromString} />
        </aside>

        <main className="relative min-w-0 flex-1">
          {totalFailure ? (
            <div className="flex h-full items-center justify-center p-6">
              <ErrorBlock
                error={snapshot.overview.error}
                title="Lakehouse API unavailable"
              />
            </div>
          ) : !booted ? (
            <div className="text-ink-faint flex h-full flex-col items-center justify-center gap-2">
              <span className="border-ink-faint size-5 animate-spin rounded-full border-2 border-t-transparent" />
              <span className="text-xs">Contacting the lakehouse…</span>
            </div>
          ) : (
            <LakeView model={lake} onFocus={focusFromString} />
          )}

          {focus && (
            <Inspector
              focus={focus}
              onClose={() => onFocus(null)}
              onFocus={onFocus}
              onMutated={refresh}
              pipelines={snapshot.pipelines.data ?? []}
            />
          )}
        </main>
      </div>

      <StreamDrawer
        events={stream}
        onFocus={focusFromString}
        onToggle={() => onStreamOpen(!streamOpen)}
        open={streamOpen}
      />

      <ConsolePalette
        onFocus={onFocus}
        onOpenChange={palette.setOpen}
        open={palette.open}
        snapshot={snapshot}
      />
    </div>
  )
}
