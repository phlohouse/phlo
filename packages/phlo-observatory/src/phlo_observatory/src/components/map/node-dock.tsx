/**
 * Node dock: the persistent inspector that opens when a map node is
 * selected. Carries the node's facts, evidence (upstream/downstream,
 * checks, recent operations), deep links into workbenches, and any
 * dataset-pipeline actions — run through the shared confirmed runner.
 */
import { Link } from '@tanstack/react-router'
import { GitBranch, ShieldCheck, Table2, Workflow, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import type {
  ObservatoryAssetDetail,
  ObservatoryDatasetPipeline,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import type { MapNodeModel } from './map-model'
import {
  getObservatoryAssetDetailDirect,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import {
  ActionBar,
  ObservActionButton,
  useActionRunner,
} from '@/components/observatory/actions'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { InspectorSection } from '@/components/observatory/split-view'
import { StatusBadge } from '@/components/observatory/status'
import { ErrorBlock, LoadingBlock } from '@/components/observatory/states'
import { formatRelativeTime } from '@/components/observatory/time'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export function NodeDock({
  node,
  pipeline,
  onClose,
  onMutated,
}: {
  node: MapNodeModel
  pipeline?: ObservatoryDatasetPipeline | null
  onClose: () => void
  onMutated?: () => void
}) {
  const [detail, setDetail] =
    useState<ObservatoryResourceResult<ObservatoryAssetDetail> | null>(null)
  const runner = useActionRunner(
    (actionId) => runObservatoryActionDirect({ actionId }),
    { onSettled: onMutated },
  )

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    void getObservatoryAssetDetailDirect({ assetId: node.id }).then((next) => {
      if (!cancelled) setDetail(next)
    })
    return () => {
      cancelled = true
    }
  }, [node.id])

  const actions = pipeline?.actions ?? []

  return (
    <aside className="border-rule bg-panel absolute inset-y-0 right-0 z-10 flex h-full w-80 max-w-[85vw] flex-none flex-col border-l shadow-xl sm:static sm:shadow-none">
      <header className="border-rule flex items-start gap-2.5 border-b px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-ink truncate text-sm font-semibold">
              {node.label}
            </h2>
            <StatusBadge state={node.state} />
          </div>
          <p className="text-ink-faint mt-0.5 text-xs">
            {node.group}
            {node.table ? ` · ${node.table}` : ''}
          </p>
        </div>
        <Button
          aria-label="Close dock"
          className="flex-none"
          onClick={onClose}
          size="icon-xs"
          variant="ghost"
        >
          <X className="size-3.5" />
        </Button>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-5 p-4">
          {actions.length > 0 && (
            <InspectorSection label="Actions">
              <ActionBar>
                {actions.map((action) => (
                  <ObservActionButton
                    action={action}
                    key={action.id}
                    onRun={runner.request}
                  />
                ))}
              </ActionBar>
            </InspectorSection>
          )}

          <InspectorSection label="Open in">
            <div className="flex flex-wrap gap-1.5">
              <DockLink
                icon={<GitBranch className="size-3" />}
                label="Lineage"
                to={`/lineage?assetId=${encodeURIComponent(node.id)}`}
              />
              <DockLink
                icon={<Workflow className="size-3" />}
                label="Dataset"
                to={`/datasets/${encodeURIComponent(node.id)}`}
              />
              {node.table && (
                <DockLink
                  icon={<Table2 className="size-3" />}
                  label="Table"
                  to={`/tables?tableId=${encodeURIComponent(node.table)}`}
                />
              )}
              {node.checksTotal > 0 && (
                <DockLink
                  icon={<ShieldCheck className="size-3" />}
                  label="Quality"
                  to="/quality"
                />
              )}
            </div>
          </InspectorSection>

          {!detail ? (
            <LoadingBlock label="Loading node evidence" rows={4} />
          ) : detail.error ? (
            <ErrorBlock error={detail.error} title="Evidence unavailable" />
          ) : (
            detail.data && <DockEvidence detail={detail.data} node={node} />
          )}
        </div>
      </ScrollArea>
      {runner.dialog}
    </aside>
  )
}

function DockLink({
  icon,
  label,
  to,
}: {
  icon: React.ReactNode
  label: string
  to: string
}) {
  return (
    <Link
      className="border-rule bg-raised text-ink-soft hover:border-ink-faint/40 hover:text-ink inline-flex h-7 items-center gap-1.5 rounded-lg border px-2.5 text-xs transition-colors"
      to={to}
    >
      {icon}
      {label}
    </Link>
  )
}

function metaText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function DockEvidence({
  detail,
  node,
}: {
  detail: ObservatoryAssetDetail
  node: MapNodeModel
}) {
  const meta = detail.asset.metadata
  const recentOps = detail.operations.slice(0, 5)
  return (
    <>
      {node.description && (
        <p className="text-ink-soft text-xs/relaxed">{node.description}</p>
      )}

      <InspectorSection label="Facts">
        <FactGrid>
          <Fact label="Group" value={node.group} />
          <Fact
            label="Schema"
            value={metaText(meta.schema) ?? metaText(meta.namespace) ?? '—'}
          />
          <Fact label="Format" value={metaText(meta.format) ?? '—'} />
          <Fact
            label="Materialized"
            value={metaText(meta.materialized) ?? '—'}
          />
          <Fact label="Kinds" value={node.kinds.join(', ')} />
        </FactGrid>
      </InspectorSection>

      <InspectorSection label="Topology">
        <FactGrid>
          <Fact
            label="Upstream"
            value={detail.upstream.map((a) => a.name).join(', ') || 'none'}
          />
          <Fact
            label="Downstream"
            value={detail.downstream.map((a) => a.name).join(', ') || 'none'}
          />
        </FactGrid>
      </InspectorSection>

      {detail.quality.length > 0 && (
        <InspectorSection label={`Checks · ${detail.quality.length}`}>
          <ul className="flex flex-col gap-1">
            {detail.quality.slice(0, 6).map((check) => (
              <li className="flex items-center gap-2 text-xs" key={check.id}>
                <StatusBadge
                  className={cn('flex-none')}
                  state={
                    check.status === 'failing'
                      ? 'error'
                      : check.status === 'warning'
                        ? 'warning'
                        : check.status === 'passing'
                          ? 'ok'
                          : 'unknown'
                  }
                />
                <span className="text-ink-soft min-w-0 truncate">
                  {check.name}
                </span>
              </li>
            ))}
          </ul>
        </InspectorSection>
      )}

      {recentOps.length > 0 && (
        <InspectorSection label={`Recent operations · ${recentOps.length}`}>
          <ul className="flex flex-col gap-1.5">
            {recentOps.map((operation) => (
              <li key={operation.id}>
                <Link
                  className="group flex items-center gap-2"
                  search={{ runId: operation.id }}
                  to="/runs"
                >
                  <StatusBadge
                    label={operation.status}
                    state={operation.health.state}
                  />
                  <span className="text-ink-soft min-w-0 flex-1 truncate text-xs group-hover:text-ink">
                    {operation.name}
                  </span>
                  <span className="text-ink-faint flex-none font-mono text-[10px]">
                    {formatRelativeTime(
                      operation.started_at ?? operation.completed_at,
                    )}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </InspectorSection>
      )}
    </>
  )
}
