/**
 * Attention rail: the left column of the console. A ranked strip of
 * everything that needs a human — clicking an item focuses the graph and
 * opens the inspector on the object behind it.
 */
import { Siren } from 'lucide-react'

import type { TriageItem } from '@/components/now/triage-model'
import { HealthDot } from '@/components/observatory/status'
import { formatRelativeTime } from '@/components/observatory/time'
import { cn } from '@/lib/utils'

const KIND_LABEL: Record<TriageItem['kind'], string> = {
  check: 'check',
  dataset: 'data',
  publish: 'pub',
  run: 'run',
  service: 'svc',
}

export function AttentionRail({
  items,
  onFocus,
}: {
  items: Array<TriageItem>
  onFocus: (focus: string) => void
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="border-rule flex h-9 flex-none items-center gap-2 border-b px-3">
        <Siren className="text-ink-faint size-3.5" />
        <span className="text-ink-soft text-[11px] font-medium tracking-wide uppercase">
          Signals
        </span>
        <span className="text-ink-faint ml-auto font-mono text-[10px]">
          {items.length}
        </span>
      </div>
      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-1.5">
        {items.length === 0 ? (
          <div className="text-ink-faint px-2 py-6 text-center text-[11px]">
            All clear — nothing needs a human.
          </div>
        ) : (
          items.map((item) => (
            <button
              className={cn(
                'group hover:bg-hover flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left transition-colors',
                item.state === 'error' && 'hover:bg-status-band-error',
                item.state === 'warning' && 'hover:bg-status-band-warning',
              )}
              key={item.id}
              onClick={() => item.focus && onFocus(item.focus)}
              title={item.reason}
              type="button"
            >
              <HealthDot className="mt-1 flex-none" state={item.state} />
              <span className="min-w-0 flex-1">
                <span className="text-ink block truncate text-xs font-medium">
                  {item.title}
                </span>
                {item.meta && (
                  <span className="text-ink-faint block truncate text-[10px]">
                    {item.meta}
                  </span>
                )}
                <span className="text-ink-faint mt-0.5 flex items-center gap-1.5 text-[10px]">
                  <span className="tracking-wide uppercase">
                    {KIND_LABEL[item.kind]}
                  </span>
                  {item.at && <span>{formatRelativeTime(item.at)}</span>}
                </span>
              </span>
            </button>
          ))
        )}
      </div>
    </div>
  )
}
