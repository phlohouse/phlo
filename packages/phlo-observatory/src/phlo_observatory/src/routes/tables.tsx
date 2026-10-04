/** Read-only v1 asset-backed Tables screen. */
import { Link, createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

import type {
  V1Asset,
  V1AssetDetail,
  V1AssetPreview,
} from '@/observatory/api/tablesV1'
import {
  getV1AssetDetail,
  getV1AssetPreview,
  getV1AssetsPage,
} from '@/observatory/api/tablesV1'
import {
  environmentChangeEvent,
  selectedEnvironment,
} from '@/observatory/api/environment'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

export const Route = createFileRoute('/tables')({ component: Tables })

type LoadState<T> =
  | { kind: 'loading' }
  | { kind: 'unavailable'; message: string }
  | { kind: 'available'; data: T }

export function Tables() {
  const [environment, setEnvironment] = useState(selectedEnvironment)
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    typeof window === 'undefined'
      ? null
      : new URLSearchParams(window.location.search).get('tableId'),
  )
  const [assets, setAssets] = useState<
    LoadState<{ items: Array<V1Asset>; nextCursor: string | null }>
  >({ kind: 'loading' })
  const [detail, setDetail] = useState<LoadState<V1AssetDetail> | null>(null)
  const [preview, setPreview] = useState<LoadState<V1AssetPreview> | null>(null)

  useEffect(() => {
    const update = () => setEnvironment(selectedEnvironment())
    window.addEventListener(environmentChangeEvent(), update)
    return () => window.removeEventListener(environmentChangeEvent(), update)
  }, [])

  useEffect(() => {
    if (!environment) return
    setAssets({ kind: 'loading' })
    void getV1AssetsPage({ data: { environment, cursor: null } }).then(
      (result) =>
        setAssets(
          result.kind === 'available'
            ? {
                kind: 'available',
                data: {
                  items: result.data.items,
                  nextCursor: result.data.next_cursor,
                },
              }
            : result,
        ),
    )
  }, [environment])

  const selected =
    assets.kind === 'available'
      ? (assets.data.items.find((asset) => asset.id === selectedId) ?? null)
      : null

  useEffect(() => {
    if (!environment || !selectedId) {
      setDetail(null)
      setPreview(null)
      return
    }
    setDetail({ kind: 'loading' })
    setPreview({ kind: 'loading' })
    void getV1AssetDetail({ data: { environment, assetId: selectedId } }).then(
      (result) =>
        setDetail(
          result.kind === 'available'
            ? { kind: 'available', data: result.data }
            : result,
        ),
    )
    void getV1AssetPreview({
      data: { environment, assetId: selectedId },
    }).then((result) =>
      setPreview(
        result.kind === 'available'
          ? { kind: 'available', data: result.data }
          : result,
      ),
    )
  }, [environment, selectedId])

  function selectAsset(assetId: string) {
    setSelectedId(assetId)
    const url = new URL(window.location.href)
    url.searchParams.set('tableId', assetId)
    window.history.replaceState(null, '', `${url.pathname}?${url.searchParams}`)
  }

  function loadMore() {
    if (!environment || assets.kind !== 'available' || !assets.data.nextCursor)
      return
    void getV1AssetsPage({
      data: { environment, cursor: assets.data.nextCursor },
    }).then((result) => {
      if (result.kind === 'unavailable') {
        setAssets(result)
      } else {
        setAssets({
          kind: 'available',
          data: {
            items: [...assets.data.items, ...result.data.items],
            nextCursor: result.data.next_cursor,
          },
        })
      }
    })
  }

  return (
    <ObservatoryPage
      kicker="Tables"
      title="Table inventory"
      description="Read-only physical-table inventory from the selected v1 environment."
    >
      {!environment ? (
        <Notice
          title="Tables unavailable"
          message="Select prod or staging before loading tables. No environment is assumed."
        />
      ) : null}
      {environment && assets.kind === 'loading' ? (
        <Notice
          title="Loading tables"
          message={`Reading v1 assets from ${environment}.`}
        />
      ) : null}
      {environment && assets.kind === 'unavailable' ? (
        <Notice title="Tables unavailable" message={assets.message} />
      ) : null}
      {assets.kind === 'available' ? (
        <div className="phlo-observatory-browser-shell">
          <section className="phlo-observatory-detail-list">
            <strong>{assets.data.items.length} loaded assets</strong>
            {assets.data.items.length === 0 ? (
              <Notice
                title="No tables available"
                message={`v1 reported no assets in ${environment}.`}
              />
            ) : null}
            {assets.data.items.map((asset) => (
              <button
                className="phlo-observatory-mini-row"
                data-active={asset.id === selectedId}
                key={asset.id}
                onClick={() => selectAsset(asset.id)}
                type="button"
              >
                <span>{asset.relation ?? asset.key.join('.')}</span>
                <small>
                  {asset.compute_kind ?? 'kind not reported'} ·{' '}
                  {asset.last_materialization_at ?? 'not materialized'}
                </small>
              </button>
            ))}
            {assets.data.nextCursor ? (
              <button onClick={loadMore} type="button">
                Load more assets
              </button>
            ) : null}
          </section>
          <aside className="phlo-observatory-inspector">
            {selectedId ? (
              <AssetInspector
                asset={selected}
                detail={detail}
                preview={preview}
              />
            ) : (
              <p>Select a table to inspect its schema and bounded preview.</p>
            )}
          </aside>
        </div>
      ) : null}
    </ObservatoryPage>
  )
}

function AssetInspector({
  asset,
  detail,
  preview,
}: {
  asset: V1Asset | null
  detail: LoadState<V1AssetDetail> | null
  preview: LoadState<V1AssetPreview> | null
}) {
  return (
    <>
      <h2>{asset?.relation ?? asset?.key.join('.') ?? 'Selected table'}</h2>
      <p>{asset?.description ?? 'No description reported.'}</p>
      {asset ? (
        <Link to="/lineage" search={{ assetId: asset.id }}>
          Open asset lineage
        </Link>
      ) : null}
      {detail?.kind === 'available' && detail.data.dependencies.length > 0 ? (
        <section aria-label="Asset dependencies">
          <h3>Dependencies</h3>
          <ul>
            {detail.data.dependencies.map((dependency) => {
              const assetId = dependency.join('/')
              return (
                <li key={assetId}>
                  <Link to="/lineage" search={{ assetId }}>
                    {dependency.join('.')}
                  </Link>
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}
      <h3>Schema</h3>
      {detail?.kind === 'available' ? (
        <ul>
          {detail.data.columns.length ? (
            detail.data.columns.map((column) => (
              <li key={column.name}>
                {column.name} · {column.type ?? 'type not reported'}
              </li>
            ))
          ) : (
            <li>No schema is available.</li>
          )}
        </ul>
      ) : (
        <State state={detail} unavailableTitle="Schema unavailable" />
      )}
      <h3>Preview</h3>
      {preview?.kind === 'available' ? (
        <Preview preview={preview.data} />
      ) : (
        <State state={preview} unavailableTitle="Preview unavailable" />
      )}
    </>
  )
}

function Preview({ preview }: { preview: V1AssetPreview }) {
  if (preview.rows.length === 0)
    return <p>No rows returned by the bounded preview.</p>
  return (
    <>
      <table className="phlo-observatory-row-preview-table">
        <thead>
          <tr>
            {preview.columns.map((column) => (
              <th key={column.name}>{column.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row, index) => (
            <tr key={index}>
              {preview.columns.map((column) => (
                <td key={column.name}>{String(row[column.name] ?? 'null')}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {preview.has_more ? (
        <p>Preview is limited to 100 rows; more rows are available.</p>
      ) : null}
    </>
  )
}

function State({
  state,
  unavailableTitle,
}: {
  state: LoadState<unknown> | null
  unavailableTitle: string
}) {
  if (!state || state.kind === 'loading') return <p>Loading…</p>
  if (state.kind === 'unavailable')
    return <Notice title={unavailableTitle} message={state.message} />
  return null
}

function Notice({ title, message }: { title: string; message: string }) {
  return (
    <div className="phlo-observatory-empty-state">
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  )
}
