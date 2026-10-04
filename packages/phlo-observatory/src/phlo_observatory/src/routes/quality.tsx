/** v1-backed quality evidence. Legacy Observatory quality routes remain untouched. */
import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'

import type {
  V1CheckExecution,
  V1QualityAsset,
} from '@/observatory/api/qualityV1'
import { getV1QualitySnapshot } from '@/observatory/api/qualityV1'
import {
  environmentChangeEvent,
  selectedEnvironment,
} from '@/observatory/api/environment'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/quality')({ component: Quality })

type QualityRow = {
  id: string
  assetId: string
  name: string
  description: string | null
  execution: V1CheckExecution | null
}

export function Quality() {
  const [environment, setEnvironment] = useState(selectedEnvironment)
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    typeof window === 'undefined'
      ? null
      : new URLSearchParams(window.location.search).get('checkId'),
  )
  useEffect(() => {
    const update = () => setEnvironment(selectedEnvironment())
    window.addEventListener(environmentChangeEvent(), update)
    return () => window.removeEventListener(environmentChangeEvent(), update)
  }, [])
  const result = useLiveResource(
    () =>
      environment
        ? getV1QualitySnapshot({ data: { environment } }).then((response) => ({
            data: response.kind === 'available' ? [response.data] : null,
            error: response.kind === 'unavailable' ? response.message : null,
          }))
        : Promise.resolve({ data: null, error: null }),
    120_000,
    `observatory:quality-v1:${environment ?? 'unselected'}`,
  )
  const snapshot = result.data?.[0]
  const rows = useMemo(
    () =>
      snapshot?.assets.flatMap((asset) =>
        asset.kind === 'available' ? rowsForAsset(asset) : [],
      ) ?? [],
    [snapshot],
  )
  const selected = rows.find((row) => row.id === selectedId) ?? rows[0] ?? null
  const unavailableAssets =
    snapshot?.assets.filter(
      (asset): asset is Extract<V1QualityAsset, { kind: 'unavailable' }> =>
        asset.kind === 'unavailable',
    ) ?? []

  function select(row: QualityRow): void {
    setSelectedId(row.id)
    const url = new URL(window.location.href)
    url.searchParams.set('checkId', row.id)
    window.history.replaceState(null, '', `${url.pathname}?${url.searchParams}`)
  }

  return (
    <ObservatoryPage
      kicker="Quality"
      title="Quality evidence"
      description="Asset-check definitions and executions from the selected v1 environment."
    >
      {!environment ? (
        <Notice detail="Select prod or staging before loading quality evidence. No environment is assumed." />
      ) : result.isLoading ? (
        <Notice
          title="Loading quality evidence"
          detail={`Reading asset checks from ${environment}.`}
        />
      ) : result.error ? (
        <Notice title="Quality evidence unavailable" detail={result.error} />
      ) : snapshot ? (
        <section className="phlo-observatory-command phlo-observatory-runs-shell">
          <div className="phlo-observatory-command-primary phlo-observatory-run-list-surface">
            <div className="phlo-observatory-command-strip phlo-observatory-run-summary">
              <Metric label="Definitions" value={rows.length} />
              <Metric
                label="Passing"
                value={rows.filter((row) => outcome(row) === 'ok').length}
              />
              <Metric
                label="Failing"
                value={rows.filter((row) => outcome(row) === 'error').length}
              />
              <Metric
                label="Unknown"
                value={rows.filter((row) => outcome(row) === 'unknown').length}
              />
            </div>
            {snapshot.truncated && (
              <Notice
                title="Asset list truncated"
                detail="Only the first 100 v1 assets are represented; remaining assets were not treated as having no checks."
              />
            )}
            {rows.length === 0 ? (
              <Notice detail="No check definitions were returned for the loaded assets." />
            ) : (
              <div className="phlo-observatory-detail-list">
                {rows.map((row) => (
                  <button
                    className="phlo-observatory-mini-row"
                    data-state={outcome(row)}
                    key={row.id}
                    onClick={() => select(row)}
                    type="button"
                  >
                    <span>
                      {row.name} · {row.assetId}
                    </span>
                    <small>
                      {outcomeLabel(row)}
                      {row.execution
                        ? ` · ${formatDate(row.execution.timestamp)}`
                        : ' · no execution evidence'}
                    </small>
                  </button>
                ))}
              </div>
            )}
            {unavailableAssets.map((asset) => (
              <Notice
                key={asset.assetId}
                title={`Checks unavailable: ${asset.assetId}`}
                detail={asset.message}
              />
            ))}
          </div>
          <aside className="phlo-observatory-inspector">
            <div className="phlo-observatory-inspector-label">
              Check evidence
            </div>
            {selected ? (
              <CheckDetail row={selected} />
            ) : (
              <p>No check selected.</p>
            )}
          </aside>
        </section>
      ) : (
        <Notice detail="Quality evidence is not available." />
      )}
    </ObservatoryPage>
  )
}

function rowsForAsset(
  asset: Extract<V1QualityAsset, { kind: 'available' }>,
): Array<QualityRow> {
  return asset.checks.definitions.map((definition) => ({
    id: `${asset.assetId}:${definition.name}`,
    assetId: asset.assetId,
    name: definition.name,
    description: definition.description,
    execution:
      asset.checks.executions
        .filter((execution) => execution.check_name === definition.name)
        .sort(
          (left, right) =>
            Date.parse(right.timestamp) - Date.parse(left.timestamp),
        )[0] ?? null,
  }))
}

function CheckDetail({ row }: { row: QualityRow }) {
  const execution = row.execution
  return (
    <div className="phlo-observatory-detail-list">
      <div className="phlo-observatory-mini-row">
        <span>{row.name}</span>
        <small>{outcomeLabel(row)}</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Asset</span>
        <a href={`/lineage?assetId=${encodeURIComponent(row.assetId)}`}>
          {row.assetId}
        </a>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Description</span>
        <small>{row.description ?? 'No description reported.'}</small>
      </div>
      {execution ? (
        <>
          <div className="phlo-observatory-mini-row">
            <span>Latest execution</span>
            <small>
              {execution.status} · {formatDate(execution.timestamp)}
            </small>
          </div>
          <div className="phlo-observatory-mini-row">
            <span>Run</span>
            <a href={`/runs?runId=${encodeURIComponent(execution.run_id)}`}>
              {execution.run_id}
            </a>
          </div>
          <div className="phlo-observatory-mini-row">
            <span>Severity</span>
            <small>{execution.severity ?? 'Not reported'}</small>
          </div>
        </>
      ) : (
        <Notice
          title="Execution unknown"
          detail="This definition has no returned execution evidence. It is not counted as passing."
        />
      )}
    </div>
  )
}

function outcome(row: QualityRow): 'ok' | 'error' | 'unknown' {
  if (row.execution?.passed === true) return 'ok'
  if (row.execution?.passed === false) return 'error'
  return 'unknown'
}

function outcomeLabel(row: QualityRow): string {
  const value = outcome(row)
  return value === 'ok' ? 'passing' : value === 'error' ? 'failing' : 'unknown'
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString()
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="phlo-observatory-command-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function Notice({
  detail,
  title = 'Quality evidence',
}: {
  detail: string
  title?: string
}) {
  return (
    <div className="phlo-observatory-empty-state">
      <strong>{title}</strong>
      <br />
      {detail}
    </div>
  )
}
