/**
 * The lake surface — the deck's center.
 *
 * Columns are the layers of the lake (the producing asset's group); cells
 * are datasets — publication state, readiness, checks, freshness — sorted
 * worst-first. The inbound strip above shows in-flight pipeline-run
 * branches. No edges, no DAG: this is the catalog, live.
 */

import { ArrowDownToLine, Boxes } from 'lucide-react'

import type { LakeCell, LakeInbound, LakeModel } from './lake-model'
import { HealthDot } from '@/components/observatory/status'
import { formatRelativeTime } from '@/components/observatory/time'
import { cn } from '@/lib/utils'

const stateRing: Record<string, string> = {
  error: 'border-print-red/50',
  warning: 'border-amber-ink/40',
}

function pubStyle(publication: string): string {
  switch (publication) {
    case 'published':
      return 'text-ok-ink'
    case 'draft':
      return 'text-ink-faint'
    default:
      return 'text-ink-soft'
  }
}

function InboundCard({
  inbound,
  onFocus,
}: {
  inbound: LakeInbound
  onFocus: (target: string) => void
}) {
  return (
    <button
      className="border-blue/40 bg-panel hover:bg-hover flex w-56 flex-none flex-col gap-1 rounded-lg border px-3 py-2 text-left shadow-lg transition-colors"
      onClick={() => onFocus(inbound.focusTarget)}
      title={inbound.targets.join(', ')}
      type="button"
    >
      <div className="flex items-center gap-1.5">
        <ArrowDownToLine className="text-blue size-3.5" />
        <span className="text-ink min-w-0 flex-1 truncate text-xs font-semibold">
          {inbound.label}
        </span>
        <HealthDot state={inbound.state} />
      </div>
      <div className="text-ink-faint flex items-center gap-1.5 text-[10px]">
        <span>
          {inbound.running} running · {inbound.total} ops
        </span>
        {inbound.failed > 0 && (
          <span className="text-print-red">{inbound.failed} failed</span>
        )}
        {inbound.startedAt && (
          <span className="ml-auto font-mono">
            {formatRelativeTime(inbound.startedAt)}
          </span>
        )}
      </div>
      <div className="text-ink-soft truncate text-[10px]">
        landing → {inbound.targets.join(', ')}
        {inbound.targetExtra > 0 && ` +${inbound.targetExtra}`}
      </div>
    </button>
  )
}

function DatasetCell({
  cell,
  onFocus,
}: {
  cell: LakeCell
  onFocus: (target: string) => void
}) {
  return (
    <button
      className={cn(
        'border-rule bg-panel hover:bg-hover group flex w-full flex-col gap-0.5 rounded-lg border px-2.5 py-2 text-left transition-colors',
        stateRing[cell.state],
        cell.focused && 'ring-blue ring-1',
      )}
      onClick={() => onFocus(cell.focusTarget)}
      type="button"
    >
      <div className="flex items-center gap-1.5">
        <HealthDot state={cell.state} />
        <span className="text-ink min-w-0 flex-1 truncate text-xs font-medium">
          {cell.name}
        </span>
        <span
          className={cn(
            'flex-none text-[9px] font-medium uppercase tracking-wider',
            pubStyle(cell.publication),
          )}
        >
          {cell.publication}
        </span>
      </div>
      <div className="text-ink-faint flex items-center gap-1.5 pl-4 text-[10px]">
        {cell.checksTotal > 0 && (
          <span className={cn(cell.checksFailing > 0 && 'text-print-red')}>
            {cell.checksFailing > 0
              ? `${cell.checksFailing}/${cell.checksTotal} checks`
              : `${cell.checksTotal} checks`}
          </span>
        )}
        {cell.freshnessAt && (
          <span>{formatRelativeTime(cell.freshnessAt)}</span>
        )}
        {cell.inbound > 0 && (
          <span className="text-blue flex items-center gap-0.5">
            <span className="bg-blue size-1 animate-pulse rounded-full" />
            {cell.inbound} inbound
          </span>
        )}
      </div>
    </button>
  )
}

export function LakeView({
  model,
  onFocus,
}: {
  model: LakeModel
  onFocus: (target: string) => void
}) {
  if (model.total === 0) {
    return (
      <div className="text-ink-faint flex h-full min-h-96 flex-col items-center justify-center gap-2">
        <Boxes className="size-4" />
        <span className="text-xs">The lake is empty</span>
        <span className="text-ink-faint max-w-56 text-center text-[10px]">
          Datasets appear here when pipelines publish to the catalog.
        </span>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-[32rem] flex-col overflow-hidden">
      {model.inbound.length > 0 && (
        <div className="border-rule flex-none border-b">
          <div className="text-ink-faint flex items-center gap-2 px-3 pt-2 text-[9px] font-semibold uppercase tracking-widest">
            <ArrowDownToLine className="size-3" />
            inbound · {model.inbound.length}
          </div>
          <div className="scrollbar-thin flex gap-2 overflow-x-auto px-3 pb-2 pt-1">
            {model.inbound.map((inbound) => (
              <InboundCard
                inbound={inbound}
                key={inbound.id}
                onFocus={onFocus}
              />
            ))}
          </div>
        </div>
      )}

      <div className="scrollbar-thin flex min-h-0 flex-1 items-stretch gap-0 overflow-x-auto">
        {model.columns.map((column) => (
          <section
            className="border-rule flex w-60 flex-none flex-col border-r last:border-r-0"
            key={column.id}
          >
            <header className="border-rule flex flex-none items-baseline gap-2 border-b px-3 py-2">
              <span className="text-ink-soft text-[10px] font-semibold uppercase tracking-widest">
                {column.label}
              </span>
              <span className="text-ink-faint font-mono text-[9px]">
                {column.count}
              </span>
              {(column.error > 0 || column.warning > 0) && (
                <span className="ml-auto flex items-center gap-1.5 text-[9px]">
                  {column.error > 0 && (
                    <span className="text-print-red">{column.error} err</span>
                  )}
                  {column.warning > 0 && (
                    <span className="text-amber-ink">
                      {column.warning} warn
                    </span>
                  )}
                </span>
              )}
            </header>
            <div className="scrollbar-thin flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-2">
              {column.cells.map((cell) => (
                <DatasetCell cell={cell} key={cell.id} onFocus={onFocus} />
              ))}
              {column.count > column.cells.length && (
                <div className="text-ink-faint px-2 py-1 text-center text-[10px]">
                  +{column.count - column.cells.length} more
                </div>
              )}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
