/**
 * The inspector: the console's single detail surface. Whatever object is
 * focused — asset, dataset, table, service, check, or operation — loads
 * its detail here with facts, evidence, related objects, and its actions
 * through the shared confirmed runner. Nothing is a page; everything is
 * a layer over the map.
 */
import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import type { FocusRef } from './store'
import type {
  ObservatoryAction,
  ObservatoryAssetDetail,
  ObservatoryDatasetPipeline,
  ObservatoryDatasetProfile,
  ObservatoryOperationDetail,
  ObservatoryQualityDetail,
  ObservatoryResourceResult,
  ObservatoryServiceDetail,
  ObservatoryTablePreview,
} from '@/observatory/api/types'
import {
  getObservatoryAssetDetailDirect,
  getObservatoryDatasetProfileDirect,
  getObservatoryOperationDetailDirect,
  getObservatoryQualityDetailDirect,
  getObservatoryServiceDetailDirect,
  getObservatoryTablePreview,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import {
  ObservActionButton,
  useActionRunner,
} from '@/components/observatory/actions'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { StatusBadge } from '@/components/observatory/status'
import { ErrorBlock, LoadingBlock } from '@/components/observatory/states'
import {
  formatDateTime,
  formatRelativeTime,
} from '@/components/observatory/time'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'

const KIND_LABEL: Record<FocusRef['kind'], string> = {
  asset: 'Asset',
  check: 'Quality check',
  dataset: 'Dataset',
  op: 'Operation',
  service: 'Service',
  table: 'Table',
}

export function Inspector({
  focus,
  pipelines,
  onFocus,
  onClose,
  onMutated,
}: {
  focus: FocusRef
  pipelines: Array<ObservatoryDatasetPipeline>
  onFocus: (next: FocusRef) => void
  onClose: () => void
  onMutated: () => void
}) {
  const [result, setResult] =
    useState<ObservatoryResourceResult<unknown> | null>(null)
  const runner = useActionRunner(
    (actionId) => runObservatoryActionDirect({ actionId }),
    { onSettled: onMutated },
  )

  useEffect(() => {
    let cancelled = false
    setResult(null)
    const load = detailLoader(focus)
    void load().then((next) => {
      if (!cancelled) setResult(next)
    })
    return () => {
      cancelled = true
    }
  }, [focus.kind, focus.id])

  const title = inspectorTitle(focus, result?.data)
  const actions = collectActions(focus, result?.data, pipelines)

  return (
    <aside className="border-rule bg-panel absolute inset-y-0 right-0 z-20 flex w-[340px] max-w-[90vw] flex-col border-l shadow-2xl">
      <header className="border-rule flex flex-none items-start gap-2 border-b px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-ink-faint text-[10px] font-medium tracking-wider uppercase">
            {KIND_LABEL[focus.kind]}
          </div>
          <h2 className="text-ink mt-0.5 truncate text-sm font-semibold">
            {title}
          </h2>
        </div>
        <Button
          aria-label="Close inspector"
          className="flex-none"
          onClick={onClose}
          size="icon-xs"
          variant="ghost"
        >
          <X className="size-3.5" />
        </Button>
      </header>

      {actions.length > 0 && (
        <div className="border-rule flex flex-none flex-wrap gap-1.5 border-b px-4 py-2.5">
          {actions.map((action) => (
            <ObservActionButton
              action={action}
              key={action.id}
              onRun={runner.request}
            />
          ))}
        </div>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-5 p-4">
          {!result ? (
            <LoadingBlock label="Loading evidence" rows={4} />
          ) : result.error ? (
            <ErrorBlock error={result.error} title="Evidence unavailable" />
          ) : (
            <InspectorBody
              focus={focus}
              onFocus={onFocus}
              result={result.data}
            />
          )}
        </div>
      </ScrollArea>
      {runner.dialog}
    </aside>
  )
}

function detailLoader(
  focus: FocusRef,
): () => Promise<ObservatoryResourceResult<unknown>> {
  switch (focus.kind) {
    case 'asset':
      return () => getObservatoryAssetDetailDirect({ assetId: focus.id })
    case 'dataset':
      return () => getObservatoryDatasetProfileDirect({ datasetId: focus.id })
    case 'table':
      return () =>
        getObservatoryTablePreview({ data: { tableId: focus.id, limit: 8 } })
    case 'service':
      return () => getObservatoryServiceDetailDirect({ serviceId: focus.id })
    case 'check':
      return () => getObservatoryQualityDetailDirect({ checkId: focus.id })
    case 'op':
      return () =>
        getObservatoryOperationDetailDirect({ operationId: focus.id })
  }
}

function inspectorTitle(focus: FocusRef, data: unknown): string {
  const record = data as Record<string, unknown> | null
  const named =
    (record?.asset as { name?: string } | undefined)?.name ??
    (record?.dataset as { name?: string } | undefined)?.name ??
    (record?.service as { name?: string } | undefined)?.name ??
    (record?.operation as { name?: string } | undefined)?.name ??
    (record?.check as { name?: string } | undefined)?.name ??
    (record?.table as { name?: string } | undefined)?.name
  return named ?? focus.id
}

function collectActions(
  focus: FocusRef,
  data: unknown,
  pipelines: Array<ObservatoryDatasetPipeline>,
): Array<ObservatoryAction> {
  const record = data as Record<string, unknown> | null
  if (!record) {
    if (focus.kind === 'asset') {
      const pipeline = pipelines.find((entry) => entry.dataset?.id === focus.id)
      return pipeline?.actions ?? []
    }
    return []
  }
  const direct = record.actions
  if (Array.isArray(direct)) return direct as Array<ObservatoryAction>
  const pipeline = record.pipeline as ObservatoryDatasetPipeline | undefined
  if (pipeline?.actions) return pipeline.actions
  return []
}

function Section({ children, label }: { children: ReactNode; label: string }) {
  return (
    <section>
      <h3 className="text-ink-faint mb-1.5 text-[10px] font-medium tracking-wider uppercase">
        {label}
      </h3>
      {children}
    </section>
  )
}

function RefRow({
  label,
  onClick,
  state,
  sub,
}: {
  label: string
  onClick?: () => void
  state?: string
  sub?: string
}) {
  return (
    <button
      className="group hover:bg-hover flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors disabled:cursor-default"
      disabled={!onClick}
      onClick={onClick}
      type="button"
    >
      <span className="text-ink group-hover:text-blue min-w-0 flex-1 truncate text-xs">
        {label}
      </span>
      {sub && (
        <span className="text-ink-faint flex-none font-mono text-[10px]">
          {sub}
        </span>
      )}
      {state && <StatusBadge className="flex-none" state={state} />}
    </button>
  )
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function InspectorBody({
  focus,
  onFocus,
  result,
}: {
  focus: FocusRef
  onFocus: (next: FocusRef) => void
  result: unknown
}) {
  switch (focus.kind) {
    case 'asset':
      return (
        <AssetBody
          detail={result as ObservatoryAssetDetail}
          onFocus={onFocus}
        />
      )
    case 'dataset':
      return (
        <DatasetBody
          onFocus={onFocus}
          profile={result as ObservatoryDatasetProfile}
        />
      )
    case 'table':
      return <TableBody preview={result as ObservatoryTablePreview} />
    case 'service':
      return (
        <ServiceBody
          detail={result as ObservatoryServiceDetail}
          onFocus={onFocus}
        />
      )
    case 'check':
      return (
        <CheckBody
          detail={result as ObservatoryQualityDetail}
          onFocus={onFocus}
        />
      )
    case 'op':
      return (
        <OperationBody
          detail={result as ObservatoryOperationDetail}
          onFocus={onFocus}
        />
      )
  }
}

function AssetBody({
  detail,
  onFocus,
}: {
  detail: ObservatoryAssetDetail
  onFocus: (next: FocusRef) => void
}) {
  const meta = detail.asset.metadata
  return (
    <>
      {detail.asset.description && (
        <p className="text-ink-soft text-xs/relaxed">
          {detail.asset.description}
        </p>
      )}
      <Section label="Facts">
        <FactGrid>
          <Fact label="Group" value={detail.asset.group} />
          <Fact
            label="Schema"
            value={text(meta.schema) ?? text(meta.namespace) ?? '—'}
          />
          <Fact label="Format" value={text(meta.format) ?? '—'} />
          <Fact label="Kinds" value={detail.asset.kinds.join(', ') || '—'} />
        </FactGrid>
      </Section>
      <Section label="Topology">
        <div className="flex flex-col">
          {detail.upstream.map((asset) => (
            <RefRow
              key={asset.id}
              label={`↑ ${asset.name}`}
              onClick={() => onFocus({ kind: 'asset', id: asset.id })}
            />
          ))}
          {detail.downstream.map((asset) => (
            <RefRow
              key={asset.id}
              label={`↓ ${asset.name}`}
              onClick={() => onFocus({ kind: 'asset', id: asset.id })}
            />
          ))}
          {detail.upstream.length === 0 && detail.downstream.length === 0 && (
            <span className="text-ink-faint px-2 text-xs">No links</span>
          )}
        </div>
      </Section>
      {detail.quality.length > 0 && (
        <Section label={`Checks · ${detail.quality.length}`}>
          <div className="flex flex-col">
            {detail.quality.slice(0, 8).map((check) => (
              <RefRow
                key={check.id}
                label={check.name}
                onClick={() => onFocus({ kind: 'check', id: check.id })}
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
            ))}
          </div>
        </Section>
      )}
      {detail.operations.length > 0 && (
        <Section label={`Runs · ${detail.operations.length}`}>
          <div className="flex flex-col">
            {detail.operations.slice(0, 6).map((operation) => (
              <RefRow
                key={operation.id}
                label={operation.name}
                onClick={() => onFocus({ kind: 'op', id: operation.id })}
                state={operation.health.state}
                sub={formatRelativeTime(
                  operation.started_at ?? operation.completed_at,
                )}
              />
            ))}
          </div>
        </Section>
      )}
      {detail.tables.length > 0 && (
        <Section label={`Tables · ${detail.tables.length}`}>
          <div className="flex flex-col">
            {detail.tables.slice(0, 6).map((table) => (
              <RefRow
                key={table.id}
                label={table.name}
                onClick={() => onFocus({ kind: 'table', id: table.id })}
                sub={table.format ?? undefined}
              />
            ))}
          </div>
        </Section>
      )}
    </>
  )
}

function DatasetBody({
  onFocus,
  profile,
}: {
  onFocus: (next: FocusRef) => void
  profile: ObservatoryDatasetProfile
}) {
  const dataset = profile.dataset
  return (
    <>
      {dataset.description && (
        <p className="text-ink-soft text-xs/relaxed">{dataset.description}</p>
      )}
      <Section label="Facts">
        <FactGrid>
          <Fact label="Readiness" value={dataset.readiness_state} />
          <Fact label="Publication" value={dataset.publication_state} />
          <Fact label="Owner" value={dataset.owner ?? '—'} />
          <Fact
            label="Classifications"
            value={dataset.classifications.join(', ') || '—'}
          />
          <Fact label="Freshness" value={profile.pipeline.freshness_state} />
        </FactGrid>
      </Section>
      {profile.asset && (
        <Section label="Asset">
          <RefRow
            label={profile.asset.name}
            onClick={() =>
              profile.asset && onFocus({ kind: 'asset', id: profile.asset.id })
            }
          />
        </Section>
      )}
      {profile.quality.length > 0 && (
        <Section label={`Checks · ${profile.quality.length}`}>
          <div className="flex flex-col">
            {profile.quality.slice(0, 8).map((check) => (
              <RefRow
                key={check.id}
                label={check.name}
                onClick={() => onFocus({ kind: 'check', id: check.id })}
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
            ))}
          </div>
        </Section>
      )}
      {profile.operations.length > 0 && (
        <Section label={`Runs · ${profile.operations.length}`}>
          <div className="flex flex-col">
            {profile.operations.slice(0, 6).map((operation) => (
              <RefRow
                key={operation.id}
                label={operation.name}
                onClick={() => onFocus({ kind: 'op', id: operation.id })}
                state={operation.health.state}
                sub={formatRelativeTime(
                  operation.started_at ?? operation.completed_at,
                )}
              />
            ))}
          </div>
        </Section>
      )}
    </>
  )
}

function TableBody({ preview }: { preview: ObservatoryTablePreview }) {
  const table = preview.table
  const columns = preview.columns.slice(0, 6)
  return (
    <>
      <Section label="Facts">
        <FactGrid>
          <Fact label="Namespace" value={table.namespace ?? '—'} />
          <Fact label="Format" value={table.format ?? '—'} />
          <Fact label="Branch" value={table.branch ?? '—'} />
          <Fact label="Columns" value={preview.columns.length} />
        </FactGrid>
      </Section>
      {preview.rows.length > 0 && (
        <Section label="Preview">
          <div className="border-rule overflow-x-auto rounded-lg border">
            <table className="w-full text-left">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th
                      className="text-ink-faint border-rule border-b px-2 py-1 text-[10px] font-medium"
                      key={column}
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 6).map((row, index) => (
                  <tr
                    className="border-rule/60 border-b last:border-0"
                    key={index}
                  >
                    {columns.map((column) => (
                      <td
                        className="text-ink-soft max-w-32 truncate px-2 py-1 font-mono text-[10px]"
                        key={column}
                      >
                        {String(row[column] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </>
  )
}

function ServiceBody({
  detail,
  onFocus,
}: {
  detail: ObservatoryServiceDetail
  onFocus: (next: FocusRef) => void
}) {
  const service = detail.service
  return (
    <>
      <Section label="Facts">
        <FactGrid>
          <Fact label="Status" value={service.status} />
          <Fact label="Kind" value={service.kind} />
          <Fact
            label="Health"
            value={service.health.message ?? service.health.state}
          />
          {service.depends_on.length > 0 && (
            <Fact label="Depends on" value={service.depends_on.join(', ')} />
          )}
        </FactGrid>
      </Section>
      {service.links.length > 0 && (
        <Section label="Consoles">
          <div className="flex flex-col">
            {service.links.slice(0, 6).map((link) => (
              <a
                className="text-blue hover:text-ink flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors"
                href={link.url}
                key={link.url}
                rel="noreferrer"
                target="_blank"
              >
                {link.label ?? link.url}
              </a>
            ))}
          </div>
        </Section>
      )}
      {detail.dependents.length > 0 && (
        <Section label={`Dependents · ${detail.dependents.length}`}>
          <div className="flex flex-col">
            {detail.dependents.slice(0, 8).map((dep) => (
              <RefRow
                key={dep.id}
                label={dep.name}
                onClick={() => onFocus({ kind: 'service', id: dep.id })}
                state={dep.health.state}
              />
            ))}
          </div>
        </Section>
      )}
    </>
  )
}

function CheckBody({
  detail,
  onFocus,
}: {
  detail: ObservatoryQualityDetail
  onFocus: (next: FocusRef) => void
}) {
  const check = detail.check
  return (
    <>
      {check.description && (
        <p className="text-ink-soft text-xs/relaxed">{check.description}</p>
      )}
      <Section label="Facts">
        <FactGrid>
          <Fact label="Status" value={check.status} />
          <Fact label="Severity" value={check.severity ?? '—'} />
          <Fact label="Blocking" value={check.blocking ? 'yes' : 'no'} />
        </FactGrid>
      </Section>
      {detail.asset && (
        <Section label="Asset">
          <RefRow
            label={detail.asset.name}
            onClick={() =>
              detail.asset && onFocus({ kind: 'asset', id: detail.asset.id })
            }
          />
        </Section>
      )}
      {detail.logs.length > 0 && (
        <Section label={`Evidence · ${detail.logs.length}`}>
          <div className="flex flex-col">
            {detail.logs.slice(0, 6).map((log) => (
              <div className="px-2 py-1.5" key={log.id}>
                <div className="text-ink-soft text-xs">{log.message}</div>
                <div className="text-ink-faint font-mono text-[10px]">
                  {log.level}
                  {log.timestamp ? ` · ${formatDateTime(log.timestamp)}` : ''}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  )
}

function OperationBody({
  detail,
  onFocus,
}: {
  detail: ObservatoryOperationDetail
  onFocus: (next: FocusRef) => void
}) {
  const operation = detail.operation
  const failure =
    text(operation.metadata.exception_message) ??
    text(operation.metadata.failure_reason) ??
    text(operation.metadata.error)
  return (
    <>
      <Section label="Facts">
        <FactGrid>
          <Fact label="Status" value={operation.status} />
          <Fact label="Kind" value={operation.kind} />
          <Fact label="Started" value={formatDateTime(operation.started_at)} />
          <Fact
            label="Completed"
            value={formatDateTime(operation.completed_at)}
          />
        </FactGrid>
      </Section>
      {failure && (
        <Section label="Failure">
          <pre className="bg-raised border-rule text-print-red overflow-x-auto rounded-lg border p-2.5 font-mono text-[10px] leading-relaxed whitespace-pre-wrap">
            {failure}
          </pre>
        </Section>
      )}
      {operation.target && (
        <Section label="Target">
          <RefRow
            label={operation.target.label ?? operation.target.id}
            onClick={() => {
              const target = operation.target
              if (!target) return
              onFocus({
                kind:
                  target.kind === 'table'
                    ? 'table'
                    : target.kind === 'service'
                      ? 'service'
                      : 'asset',
                id: target.id,
              })
            }}
          />
        </Section>
      )}
      {detail.logs.length > 0 && (
        <Section label={`Logs · ${detail.logs.length}`}>
          <div className="flex flex-col">
            {detail.logs.slice(0, 8).map((log) => (
              <div className="px-2 py-1.5" key={log.id}>
                <div className="text-ink-soft text-xs">{log.message}</div>
                <div className="text-ink-faint font-mono text-[10px]">
                  {log.level}
                  {log.timestamp ? ` · ${formatDateTime(log.timestamp)}` : ''}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  )
}
