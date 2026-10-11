/** Lists environment-scoped assets with layer, health, and name filters. */
import * as React from 'react'
import { Link, createFileRoute } from '@tanstack/react-router'
import { SearchIcon } from 'lucide-react'
import { z } from 'zod'
import { useTable } from '@tanstack/react-table'
import type { Env, Layer } from '@/lib/data/types'
import type { ApiAsset } from '@/lib/data/api/assets'
import type { SortingState } from '@tanstack/react-table'
import {
  assetFreshness,
  assetListCursorForEnv,
  assetListPosition,
  assetWriteSummary,
  getAssetList,
  assetLayer as layerOf,
  nextAssetListPage,
  previousAssetListPage,
} from '@/lib/data/api/assets'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { LayerSwatch, Mono } from '@/components/phlo/status'
import { SlaLegend } from '@/components/assets/bits'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import {
  assetSortOptions,
  assetTableColumns,
  assetTableFeatures,
  formatAssetTimestamp,
} from '@/lib/data/assets-table'

type Chip = 'all' | 'attention' | Layer

export const Route = createFileRoute('/_app/assets/')({
  validateSearch: z.object({
    q: z.string().default(''),
    filter: z.enum(['all', 'attention']).optional(),
    layer: z.enum(['bronze', 'silver', 'gold']).optional(),
    cursor: z.string().optional(),
    cursorEnv: z.enum(['prod', 'staging']).optional(),
    previousCursors: z.array(z.string()).default([]),
  }),
  loaderDeps: ({ search }) => ({
    env: search.env,
    cursor: search.cursor,
    cursorEnv: search.cursorEnv,
  }),
  loader: ({ deps }) =>
    getAssetList({
      data: {
        env: deps.env,
        cursor: assetListCursorForEnv(deps.cursor, deps.cursorEnv, deps.env),
      },
    }),
  head: () => ({ meta: [{ title: 'Assets · phlo' }] }),
  component: AssetsPage,
})

const chips: Array<{ key: Chip; label: string; layer?: Layer }> = [
  { key: 'all', label: 'All' },
  { key: 'attention', label: 'Needs attention' },
  { key: 'bronze', label: 'Bronze', layer: 'bronze' },
  { key: 'silver', label: 'Silver', layer: 'silver' },
  { key: 'gold', label: 'Gold', layer: 'gold' },
]
const grid =
  'grid grid-cols-[minmax(250px,2.4fr)_84px_128px_128px_80px_80px_128px_190px] items-center gap-x-4 px-5'

function matchesAssetSearch(asset: ApiAsset, active: Chip, q: string) {
  return (
    (active === 'all' || active === 'attention' || layerOf(asset) === active) &&
    (active !== 'attention' || assetFreshness(asset) !== 'fresh') &&
    (!q ||
      [asset.id, asset.group_name ?? '', asset.description ?? ''].some(
        (value) => value.toLowerCase().includes(q),
      ))
  )
}

function countAssets(items: Array<ApiAsset>, key: Chip) {
  if (key === 'attention')
    return items.filter((asset) => assetFreshness(asset) !== 'fresh').length
  if (key === 'all') return items.length
  return items.filter((asset) => layerOf(asset) === key).length
}

function assetFilterSearch(key: Chip): {
  layer: Layer | undefined
  filter: 'all' | 'attention' | undefined
} {
  return {
    layer:
      key === 'bronze' || key === 'silver' || key === 'gold' ? key : undefined,
    filter:
      key === 'attention' ? 'attention' : key === 'all' ? 'all' : undefined,
  }
}

function AssetsPage() {
  const { items, env, next_cursor } = Route.useLoaderData()
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const cursor = assetListCursorForEnv(search.cursor, search.cursorEnv, env)
  const page = cursor ? search.previousCursors.length + 2 : 1
  const position = assetListPosition(page, items.length)
  const loaded = position.loaded
  const active: Chip = search.layer ?? search.filter ?? 'all'
  const q = search.q.trim().toLowerCase()
  const [sorting, setSorting] = React.useState<SortingState>([
    { id: 'asset', desc: false },
  ])
  const rows = React.useMemo(
    () => items.filter((asset) => matchesAssetSearch(asset, active, q)),
    [active, items, q],
  )
  const table = useTable({
    features: assetTableFeatures,
    columns: assetTableColumns,
    data: rows,
    getRowId: (asset) => asset.id,
    state: { sorting },
    onSortingChange: setSorting,
    enableSortingRemoval: false,
    enableMultiSort: false,
  })
  const displayedRows = table.getRowModel().rows
  const count = (key: Chip) => countAssets(items, key)
  const pick = (key: Chip) =>
    void navigate({
      search: (previous) => ({
        ...previous,
        cursor: undefined,
        cursorEnv: undefined,
        previousCursors: [],
        ...assetFilterSearch(key),
      }),
    })
  const resetPage = (previous: typeof search) => ({
    ...previous,
    cursor: undefined,
    cursorEnv: undefined,
    previousCursors: [],
  })
  const nextPage = () =>
    void navigate({
      search: (previous) => ({
        ...previous,
        ...nextAssetListPage(
          {
            cursor: previous.cursor,
            cursorEnv: previous.cursorEnv,
            previousCursors: previous.previousCursors,
          },
          next_cursor,
          env,
        ),
      }),
    })
  const previousPage = () =>
    void navigate({
      search: (previous) => ({
        ...previous,
        ...previousAssetListPage(
          {
            cursor: previous.cursor,
            cursorEnv: previous.cursorEnv,
            previousCursors: previous.previousCursors,
          },
          env,
        ),
      }),
    })

  return (
    <>
      <PageHeader
        title="Assets"
        meta={
          <>
            {loaded} assets loaded in <Mono>{env}</Mono>
            {' · '}
            <Link to="/datasets" className="text-primary hover:underline">
              Governed Datasets
            </Link>
          </>
        }
        actions={
          <label className="hidden h-8 w-[280px] items-center gap-2 rounded-lg border border-border bg-raised px-2.5 text-muted-foreground focus-within:border-primary lg:flex">
            <SearchIcon className="size-3.5" aria-hidden />
            <span className="sr-only">Filter assets</span>
            <input
              type="search"
              value={search.q}
              onChange={(event) =>
                void navigate({
                  search: (previous) => ({
                    ...resetPage(previous),
                    q: event.target.value,
                  }),
                  replace: true,
                })
              }
              placeholder="Filter by name, group or description"
              className="min-w-0 flex-1 border-0 bg-transparent text-[13.5px] text-foreground outline-none placeholder:text-faint"
            />
          </label>
        }
      />
      {active === 'attention' ? (
        <div
          role="note"
          className="border-b border-warn bg-warn-wash px-4 py-2 text-sm text-warn-ink"
        >
          Assets with stale or unknown freshness evidence are shown. Freshness
          is calculated from the inventory SLA and last materialization.
        </div>
      ) : null}
      <div className="flex shrink-0 flex-col gap-2.5 border-b border-line px-4 py-3 lg:px-5 lg:py-3.5">
        <label className="flex flex-col gap-1.5 lg:hidden">
          <span className="text-[13.5px] font-medium">Find an asset</span>
          <input
            type="search"
            value={search.q}
            onChange={(event) =>
              void navigate({
                search: (previous) => ({
                  ...resetPage(previous),
                  q: event.target.value,
                }),
                replace: true,
              })
            }
            placeholder="Name, group or description"
            className="h-11 rounded-[10px] border border-border-strong bg-card px-3 text-base outline-none"
          />
        </label>
        <div className="flex items-center gap-2">
          <div
            role="group"
            aria-label="Filter assets"
            className="flex min-w-0 flex-1 flex-wrap gap-2 lg:flex-none lg:flex-nowrap"
          >
            {chips.map((chip) => (
              <button
                key={chip.key}
                type="button"
                aria-pressed={active === chip.key}
                onClick={() => pick(chip.key)}
                className={cn(
                  'flex h-11 shrink-0 items-center gap-1.5 rounded-full border px-3.5 text-sm whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50 lg:h-[30px] lg:px-3 lg:text-[13px]',
                  active === chip.key
                    ? 'border-foreground bg-foreground text-background'
                    : 'border-border bg-card text-text-2 hover:bg-soft',
                )}
              >
                {chip.layer ? <LayerSwatch layer={chip.layer} /> : null}
                {chip.label}
                <span
                  className={cn(
                    'font-mono text-xs',
                    active === chip.key
                      ? 'opacity-75'
                      : 'text-muted-foreground',
                  )}
                >
                  {count(chip.key)}
                </span>
              </button>
            ))}
          </div>
          <span className="ml-auto hidden text-[13px] text-muted-foreground lg:inline">
            Sorting applies to this page
          </span>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 pt-3 pb-5 md:hidden">
        <MobileSortControls sorting={sorting} setSorting={setSorting} />
        <Eyebrow>
          Showing {rows.length} matching assets from {items.length} on page{' '}
          {page}
          {items.length
            ? ` · loaded rows ${position.start}–${position.end}`
            : null}
        </Eyebrow>
        {rows.length ? (
          <Card className="shrink-0 overflow-hidden">
            {displayedRows.map((row) => (
              <MobileRow key={row.id} asset={row.original} env={env} />
            ))}
          </Card>
        ) : (
          <NoMatch />
        )}
        <div className="flex shrink-0 items-center justify-between gap-3 text-sm text-muted-foreground">
          <span>
            {loaded} assets loaded so far
            {next_cursor ? ' · more available' : ''}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={!cursor}
              onClick={previousPage}
              className="min-h-10 rounded-lg border border-border px-3 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={!next_cursor}
              onClick={nextPage}
              className="min-h-10 rounded-lg border border-border px-3 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      </div>
      <div className="hidden min-h-0 flex-1 flex-col md:flex">
        <div className="min-h-0 flex-1 overflow-auto">
          <div role="table" aria-label="Assets" className="min-w-[1180px]">
            <div
              role="row"
              className={cn(
                grid,
                'sticky top-0 z-10 h-[38px] bg-raised text-xs tracking-wide text-muted-foreground',
              )}
            >
              {table.getHeaderGroups()[0]?.headers.map((header) => {
                const direction = header.column.getIsSorted()
                const label =
                  assetSortOptions.find(
                    (option) => option.id === header.column.id,
                  )?.label ?? 'Observed writes'
                const nextDirection =
                  header.column.getNextSortingOrder() === 'desc'
                    ? 'descending'
                    : 'ascending'
                return (
                  <span
                    key={header.id}
                    role="columnheader"
                    aria-sort={
                      direction
                        ? direction === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : 'none'
                    }
                    className={
                      header.column.id === 'rowCount' ||
                      header.column.id === 'sizeBytes'
                        ? 'text-right'
                        : undefined
                    }
                  >
                    {header.column.getCanSort() ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        title={`Sort ${label} ${nextDirection}`}
                        className={cn(
                          'inline-flex min-h-9 items-center gap-1 rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-primary',
                          header.column.id === 'rowCount' ||
                            header.column.id === 'sizeBytes'
                            ? 'justify-end'
                            : undefined,
                        )}
                      >
                        <table.FlexRender header={header} />
                        <span aria-hidden="true" className="text-[11px]">
                          {direction === 'asc'
                            ? '↑'
                            : direction === 'desc'
                              ? '↓'
                              : '↕'}
                        </span>
                      </button>
                    ) : (
                      <table.FlexRender header={header} />
                    )}
                  </span>
                )
              })}
            </div>
            {displayedRows.map((row) => (
              <div
                key={row.id}
                role="row"
                className={cn(
                  grid,
                  'h-[46px] border-b border-line-soft hover:bg-raised',
                )}
              >
                {row.getAllCells().map((cell) => (
                  <div
                    key={cell.id}
                    role="cell"
                    className={cn(
                      'min-w-0 text-[13px] text-muted-foreground',
                      cell.column.id === 'rowCount' ||
                        cell.column.id === 'sizeBytes'
                        ? 'text-right'
                        : undefined,
                    )}
                  >
                    <DesktopCell
                      columnId={cell.column.id}
                      asset={row.original}
                      env={env}
                    />
                  </div>
                ))}
              </div>
            ))}
            {rows.length === 0 ? (
              <div role="row" className={grid}>
                <div role="cell" aria-colspan={8} className="col-span-full p-5">
                  <NoMatch />
                </div>
              </div>
            ) : null}
          </div>
        </div>
        <div className="flex min-h-11 shrink-0 items-center gap-4 border-t border-line px-5 py-2 text-[13px] text-muted-foreground">
          <span>
            Showing {rows.length} matching assets from {items.length} on page{' '}
            {page} · {loaded} assets loaded so far
            {next_cursor ? ' · more available' : ''}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              disabled={!cursor}
              onClick={previousPage}
              className="rounded-md border border-border px-3 py-1.5 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={!next_cursor}
              onClick={nextPage}
              className="rounded-md border border-border px-3 py-1.5 disabled:opacity-40"
            >
              Next
            </button>
          </div>
          <SlaLegend />
        </div>
      </div>
    </>
  )
}

function MobileSortControls({
  sorting,
  setSorting,
}: {
  sorting: SortingState
  setSorting: React.Dispatch<React.SetStateAction<SortingState>>
}) {
  const current = sorting[0] ?? { id: 'asset', desc: false }
  const direction = current.desc ? 'Descending' : 'Ascending'
  const currentLabel =
    assetSortOptions.find((option) => option.id === current.id)?.label ??
    'Asset'

  return (
    <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
      <label className="flex items-center gap-2">
        <span>Sort by</span>
        <select
          value={current.id}
          onChange={(event) =>
            setSorting([{ id: event.target.value, desc: current.desc }])
          }
          className="h-9 rounded-md border border-border bg-card px-2 text-foreground"
        >
          {assetSortOptions.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        onClick={() => setSorting([{ id: current.id, desc: !current.desc }])}
        aria-label={`Sort ${currentLabel} ${current.desc ? 'ascending' : 'descending'}`}
        className="min-h-9 rounded-md border border-border bg-card px-2.5 text-foreground"
      >
        {direction}
      </button>
      <span className="ml-auto text-right text-[11px]">
        Sorting applies to this page
      </span>
    </div>
  )
}

type DesktopCellRenderer = (asset: ApiAsset, env: Env) => React.ReactNode

const desktopCellRenderers: Record<string, DesktopCellRenderer> = {
  asset: (asset, env) => (
    <Link
      to="/assets/$assetId"
      params={{ assetId: asset.id }}
      search={{ env }}
      className="block truncate font-mono text-[13px] text-foreground hover:text-link"
    >
      {asset.id}
    </Link>
  ),
  layer: (asset) => {
    const layer = layerOf(asset)
    return layer ? (
      <>
        <LayerSwatch layer={layer} />{' '}
        <span className="capitalize">{layer}</span>
      </>
    ) : (
      'Unknown'
    )
  },
  freshness: (asset) => <FreshnessCell asset={asset} />,
  lastMaterialized: (asset) => (
    <time
      dateTime={asset.last_materialization_at ?? undefined}
      title={asset.last_materialization_at ?? undefined}
      className="block whitespace-pre-line leading-tight"
    >
      {formatAssetTimestamp(asset.last_materialization_at).replace(', ', '\n')}
    </time>
  ),
  rowCount: (asset) =>
    asset.row_count?.toLocaleString() ??
    (asset.table_metadata_error ? 'Unavailable' : 'Not observed'),
  sizeBytes: (asset) =>
    asset.size_bytes == null
      ? asset.table_metadata_error
        ? 'Unavailable'
        : 'Not observed'
      : `${asset.size_bytes.toLocaleString()} B`,
  owner: (asset) => (
    <span title={asset.owner ?? 'Not declared'} className="block truncate">
      {asset.owner ?? 'Not declared'}
    </span>
  ),
  writes: (asset) => (
    <span
      title={`${assetWriteSummary(asset)}; observed write counts are not table totals.${asset.materialization_history_truncated ? ' History is truncated.' : ''}`}
      className="block truncate"
    >
      {assetWriteSummary(asset)}
    </span>
  ),
}

function DesktopCell({
  columnId,
  asset,
  env,
}: {
  columnId: string
  asset: ApiAsset
  env: Env
}) {
  return desktopCellRenderers[columnId]?.(asset, env) ?? null
}

function FreshnessCell({ asset }: { asset: ApiAsset }) {
  const sourceLabel =
    asset.freshness_source === 'iceberg_snapshot'
      ? 'Iceberg'
      : asset.freshness_source === 'dagster_materialization'
        ? 'Dagster'
        : (asset.freshness_reason?.replaceAll('_', ' ') ?? 'No evidence')
  const observed = asset.freshness_observed_at
    ? new Date(asset.freshness_observed_at)
    : null
  const observedLabel =
    observed && Number.isFinite(observed.getTime())
      ? observed.toLocaleString('en-GB', {
          hour: '2-digit',
          minute: '2-digit',
          timeZone: 'UTC',
          timeZoneName: 'short',
        })
      : asset.freshness_observed_at
  const evidenceTitle = [
    asset.freshness_source === 'iceberg_snapshot'
      ? 'Selected-ref Iceberg snapshot'
      : asset.freshness_source === 'dagster_materialization'
        ? 'Accepted Dagster materialization'
        : 'No observation',
    asset.freshness_observed_at,
    asset.freshness_reason?.replaceAll('_', ' '),
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <span
      title={evidenceTitle}
      className="flex min-w-0 flex-col text-[12px] text-muted-foreground"
    >
      <span className="text-[13px] capitalize">{assetFreshness(asset)}</span>
      <span className="truncate">
        {observedLabel ? `${sourceLabel} · ${observedLabel}` : sourceLabel}
      </span>
    </span>
  )
}

function MobileRow({ asset, env }: { asset: ApiAsset; env: Env }) {
  const layer = layerOf(asset)
  const freshness = assetFreshness(asset)
  return (
    <Link
      to="/assets/$assetId"
      params={{ assetId: asset.id }}
      search={{ env }}
      className="flex min-h-[60px] items-center gap-3 border-b border-line-soft px-3.5 py-2.5 text-foreground last:border-b-0 hover:bg-raised"
    >
      <span
        aria-label={`Freshness ${freshness}`}
        className={cn(
          'size-2.5 shrink-0 rounded-full',
          freshness === 'fresh'
            ? 'bg-ok'
            : freshness === 'stale'
              ? 'bg-bad'
              : 'bg-border-strong',
        )}
      />
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="truncate font-mono text-[13px]">{asset.id}</span>
        <span className="flex items-center gap-1.5 overflow-hidden text-[12.5px] whitespace-nowrap text-muted-foreground">
          {layer ? <LayerSwatch layer={layer} /> : null}
          <span className="capitalize">{layer ?? 'Unknown layer'}</span>
          <span className="truncate">{asset.group_name ?? 'No group'}</span>
        </span>
      </span>
      <span className="shrink-0 text-xs whitespace-nowrap text-muted-foreground">
        {freshness === 'fresh'
          ? 'Fresh'
          : freshness === 'stale'
            ? 'Stale'
            : 'Freshness unknown'}
      </span>
    </Link>
  )
}

function NoMatch() {
  return (
    <Card className="items-center gap-1.5 px-4 py-6 text-center">
      <div className="text-[15px] font-medium">No assets match</div>
      <div className="text-[13px] text-muted-foreground">
        Try another name, or pick All.
      </div>
    </Card>
  )
}
