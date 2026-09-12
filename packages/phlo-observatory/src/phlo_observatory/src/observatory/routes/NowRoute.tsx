/**
 * /now — the ranked triage posture. One queue of everything needing a
 * human: failed runs, failing checks, stale datasets, degraded services.
 * Ranked by severity + blast radius + recency; each item links into the
 * workbench that owns it, and dataset items expose their pipeline actions
 * through the shared confirmed runner.
 */
import { Link } from '@tanstack/react-router'
import {
  Database,
  ListChecks,
  RefreshCcw,
  Server,
  Workflow,
} from 'lucide-react'
import { useEffect, useMemo, useReducer, useState } from 'react'

import type {
  ObservatoryDatasetPipeline,
  ObservatoryLogEvent,
  ObservatoryOperation,
  ObservatoryQualityCheck,
  ObservatoryResourceResult,
  ObservatoryService,
} from '@/observatory/api/types'
import type { TriageItem, TriageKind } from '@/components/now/triage-model'
import {
  getObservatoryLogRecords,
  getObservatoryOperationRecords,
  getObservatoryPipelineRecords,
  getObservatoryQualityRecords,
  getObservatoryServices,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import { loadCachedResource } from '@/observatory/routes/liveResource'
import { buildTriageQueue } from '@/components/now/triage-model'
import {
  ActionBar,
  ObservActionButton,
  useActionRunner,
} from '@/components/observatory/actions'
import { Page, PageHeader } from '@/components/observatory/page'
import { HealthDot, StatusBadge } from '@/components/observatory/status'
import { EmptyBlock } from '@/components/observatory/states'
import { formatRelativeTime } from '@/components/observatory/time'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type NowState = {
  logs: ObservatoryResourceResult<Array<ObservatoryLogEvent>>
  operations: ObservatoryResourceResult<Array<ObservatoryOperation>>
  pipelines: ObservatoryResourceResult<Array<ObservatoryDatasetPipeline>>
  quality: ObservatoryResourceResult<Array<ObservatoryQualityCheck>>
  services: ObservatoryResourceResult<Array<ObservatoryService>>
}

const KIND_META: Record<TriageKind, { label: string; icon: typeof Workflow }> =
  {
    check: { label: 'Check', icon: ListChecks },
    dataset: { label: 'Dataset', icon: Database },
    run: { label: 'Run', icon: Workflow },
    service: { label: 'Service', icon: Server },
  }

const FILTERS: Array<{ id: TriageKind | 'all'; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'run', label: 'Runs' },
  { id: 'check', label: 'Checks' },
  { id: 'dataset', label: 'Datasets' },
  { id: 'service', label: 'Services' },
]

export function NowRoute({
  filter,
  onFilter,
}: {
  filter: TriageKind | 'all'
  onFilter: (kind: TriageKind | 'all') => void
}) {
  const [state, setState] = useReducer(
    (current: NowState, patch: Partial<NowState>) => ({ ...current, ...patch }),
    {
      logs: { data: null, error: null },
      operations: { data: null, error: null },
      pipelines: { data: null, error: null },
      quality: { data: null, error: null },
      services: { data: null, error: null },
    },
  )
  const [reloading, setReloading] = useState(false)

  useEffect(() => {
    let cancelled = false
    function load(force = false) {
      const requests: Array<
        [keyof NowState, () => Promise<ObservatoryResourceResult<unknown>>]
      > = [
        ['operations', getObservatoryOperationRecords],
        ['quality', getObservatoryQualityRecords],
        ['pipelines', getObservatoryPipelineRecords],
        ['services', getObservatoryServices],
        ['logs', getObservatoryLogRecords],
      ]
      for (const [field, loader] of requests) {
        void loadCachedResource(`observatory:${field}`, loader, {
          force,
          staleMs: 30_000,
        }).then((next) => {
          if (!cancelled) setState({ [field]: next } as Partial<NowState>)
        })
      }
    }
    load(true)
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') load(true)
    }, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  const queue = useMemo(
    () =>
      buildTriageQueue({
        operations: state.operations.data ?? [],
        quality: state.quality.data ?? [],
        pipelines: state.pipelines.data ?? [],
        services: state.services.data ?? [],
        logs: state.logs.data ?? [],
      }),
    [state],
  )
  const visible =
    filter === 'all' ? queue : queue.filter((item) => item.kind === filter)
  const counts = useMemo(() => {
    const byKind = new Map<TriageKind, number>()
    for (const item of queue) {
      byKind.set(item.kind, (byKind.get(item.kind) ?? 0) + 1)
    }
    return byKind
  }, [queue])

  const loading =
    !state.operations.data && !state.quality.data && !state.pipelines.data

  const reload = () => {
    setReloading(true)
    const loaders: Array<
      [keyof NowState, () => Promise<ObservatoryResourceResult<unknown>>]
    > = [
      ['operations', getObservatoryOperationRecords],
      ['quality', getObservatoryQualityRecords],
      ['pipelines', getObservatoryPipelineRecords],
      ['services', getObservatoryServices],
      ['logs', getObservatoryLogRecords],
    ]
    void Promise.all(
      loaders.map(([field, loader]) =>
        loadCachedResource(`observatory:${field}`, loader, {
          force: true,
          staleMs: 0,
        }).then((next) => setState({ [field]: next } as Partial<NowState>)),
      ),
    ).finally(() => setReloading(false))
  }

  return (
    <Page>
      <PageHeader
        actions={
          <div className="flex items-center gap-2">
            <StatusBadge
              label={
                queue.length === 0 ? 'Queue clear' : `${queue.length} open`
              }
              state={queue.length === 0 ? 'ok' : 'warning'}
            />
            <Button
              disabled={reloading}
              onClick={reload}
              size="sm"
              variant="outline"
            >
              <RefreshCcw
                className={cn('size-3.5', reloading && 'animate-spin')}
              />
              Refresh
            </Button>
          </div>
        }
        description="Everything in the lakehouse that needs a human, ranked by severity and blast radius."
        title="Now"
      />

      <div className="flex flex-wrap items-center gap-1.5">
        {FILTERS.map(({ id, label }) => {
          const count = id === 'all' ? queue.length : (counts.get(id) ?? 0)
          return (
            <button
              className={cn(
                'flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors',
                filter === id
                  ? 'border-blue/50 bg-blue-soft text-blue'
                  : 'border-rule text-ink-soft hover:border-foreground/25 hover:text-ink',
              )}
              key={id}
              onClick={() => onFilter(id)}
              type="button"
            >
              {label}
              <span className="text-[10px] opacity-70">{count}</span>
            </button>
          )
        })}
      </div>

      {loading ? (
        <div className="text-ink-faint py-16 text-center text-xs">
          Scanning the lakehouse…
        </div>
      ) : visible.length === 0 ? (
        <EmptyBlock
          description={
            filter === 'all'
              ? 'No failed runs, failing checks, stale datasets, or degraded services.'
              : `No ${filter} items in the queue.`
          }
          title="Queue clear"
        />
      ) : (
        <ol className="flex flex-col gap-2">
          {visible.map((item, index) => (
            <TriageRow
              item={item}
              key={item.id}
              onMutated={reload}
              pipelines={state.pipelines.data ?? []}
              rank={index + 1}
            />
          ))}
        </ol>
      )}
    </Page>
  )
}

function TriageRow({
  item,
  rank,
  pipelines,
  onMutated,
}: {
  item: TriageItem
  rank: number
  pipelines: Array<ObservatoryDatasetPipeline>
  onMutated: () => void
}) {
  const meta = KIND_META[item.kind]
  const Icon = meta.icon
  const pipeline = item.datasetId
    ? pipelines.find((entry) => entry.dataset?.id === item.datasetId)
    : undefined
  const runner = useActionRunner(
    (actionId) => runObservatoryActionDirect({ actionId }),
    { onSettled: onMutated },
  )

  return (
    <li
      className={cn(
        'bg-panel border-rule rounded-xl border px-3.5 py-3',
        item.state === 'error' && 'border-print-red/30',
        item.state === 'warning' && 'border-amber-ink/30',
      )}
    >
      <div className="flex items-start gap-3">
        <span className="text-ink-faint w-6 flex-none pt-0.5 text-right font-mono text-[10px]">
          {String(rank).padStart(2, '0')}
        </span>
        <Icon
          className={cn(
            'mt-0.5 size-4 flex-none',
            item.state === 'error'
              ? 'text-print-red'
              : item.state === 'warning'
                ? 'text-amber-ink'
                : 'text-ink-faint',
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Link
              className="text-ink hover:text-blue truncate text-[13px] font-semibold"
              to={item.href}
            >
              {item.title}
            </Link>
            <span
              className={cn(
                'rounded-full px-1.5 py-px text-[10px] font-medium',
                item.state === 'error'
                  ? 'bg-status-band-error text-print-red'
                  : item.state === 'warning'
                    ? 'bg-status-band-warning text-amber-ink'
                    : 'bg-hover text-ink-soft',
              )}
            >
              {meta.label}
            </span>
          </div>
          <p className="text-ink-soft mt-1 text-xs">{item.reason}</p>
          {item.meta && (
            <p className="text-ink-faint mt-0.5 truncate font-mono text-[10px]">
              {item.meta}
            </p>
          )}
          {pipeline && pipeline.actions.length > 0 && (
            <ActionBar>
              {pipeline.actions.map((action) => (
                <ObservActionButton
                  action={action}
                  key={action.id}
                  onRun={runner.request}
                />
              ))}
            </ActionBar>
          )}
        </div>
        <div className="flex flex-none flex-col items-end gap-1.5">
          <HealthDot state={item.state} />
          {item.at && (
            <span className="text-ink-faint font-mono text-[10px]">
              {formatRelativeTime(item.at)}
            </span>
          )}
        </div>
      </div>
      {runner.dialog}
    </li>
  )
}
