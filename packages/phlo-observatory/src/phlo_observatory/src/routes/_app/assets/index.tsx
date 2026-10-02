/** Lists environment-scoped assets with layer, health, and name filters. */
import * as React from 'react'
import { Link, createFileRoute } from '@tanstack/react-router'
import { SearchIcon } from 'lucide-react'
import { z } from 'zod'
import type { Env, Layer } from '@/lib/data/types'
import type { ApiAsset } from '@/lib/data/api/assets'
import { getAssetList, assetLayer as layerOf } from '@/lib/data/api/assets'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { LayerSwatch, Mono } from '@/components/phlo/status'
import { SlaLegend } from '@/components/assets/bits'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

type Chip = 'all' | 'attention' | Layer

export const Route = createFileRoute('/_app/assets/')({
  validateSearch: z.object({
    q: z.string().default(''),
    filter: z.enum(['all', 'attention']).optional(),
    layer: z.enum(['bronze', 'silver', 'gold']).optional(),
  }),
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getAssetList({ data: deps.env }),
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
  'grid grid-cols-[minmax(250px,2.4fr)_84px_128px_128px_80px_80px_104px_104px] items-center gap-x-4 px-5'

function AssetsPage() {
  const { items, env, next_cursor } = Route.useLoaderData()
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const active: Chip = search.layer ?? search.filter ?? 'all'
  const q = search.q.trim().toLowerCase()
  const rows = React.useMemo(
    () =>
      items.filter(
        (asset) =>
          (active === 'all' || layerOf(asset) === active) &&
          (!q ||
            [asset.id, asset.group_name ?? '', asset.description ?? ''].some(
              (value) => value.toLowerCase().includes(q),
            )),
      ),
    [active, items, q],
  )
  const count = (key: Chip) =>
    key === 'attention'
      ? '—'
      : key === 'all'
        ? items.length
        : items.filter((asset) => layerOf(asset) === key).length
  const pick = (key: Chip) =>
    void navigate({
      search: (previous) => ({
        ...previous,
        layer:
          key === 'bronze' || key === 'silver' || key === 'gold'
            ? key
            : undefined,
        filter:
          key === 'attention' ? 'attention' : key === 'all' ? 'all' : undefined,
      }),
    })

  return (
    <>
      <PageHeader
        title="Assets"
        meta={
          <>
            {items.length} assets in <Mono>{env}</Mono>
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
                    ...previous,
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
          The Attention filter is unavailable because freshness is not supplied
          by the asset inventory API. Choose All or a layer to browse assets.
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
                search: (previous) => ({ ...previous, q: event.target.value }),
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
            className="-mx-4 flex min-w-0 flex-1 gap-2 overflow-x-auto px-4 [scrollbar-width:none] lg:mx-0 lg:flex-none lg:px-0"
          >
            {chips.map((chip) => (
              <button
                key={chip.key}
                type="button"
                aria-pressed={active === chip.key}
                disabled={chip.key === 'attention'}
                title={
                  chip.key === 'attention'
                    ? 'Freshness observations are unavailable.'
                    : undefined
                }
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
            Sorted by asset name
          </span>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 pt-3 pb-5 md:hidden">
        <Eyebrow>
          Showing {rows.length} of {items.length}
        </Eyebrow>
        {rows.length ? (
          <Card className="shrink-0 overflow-hidden">
            {rows.map((asset) => (
              <MobileRow key={asset.id} asset={asset} env={env} />
            ))}
          </Card>
        ) : (
          <NoMatch />
        )}
      </div>
      <div className="hidden min-h-0 flex-1 flex-col md:flex">
        <div className="min-h-0 flex-1 overflow-auto">
          <div role="table" aria-label="Assets" className="min-w-[1040px]">
            <div
              role="row"
              className={cn(
                grid,
                'sticky top-0 z-10 h-[38px] bg-raised text-xs tracking-wide text-muted-foreground',
              )}
            >
              {[
                'Asset',
                'Layer',
                'Freshness',
                'Last materialized',
                'Rows',
                'Size',
                'Owner',
                'Last 7 days',
              ].map((label) => (
                <span
                  key={label}
                  role="columnheader"
                  className={
                    label === 'Rows' || label === 'Size'
                      ? 'text-right'
                      : undefined
                  }
                >
                  {label}
                </span>
              ))}
            </div>
            {rows.map((asset) => (
              <DesktopRow key={asset.id} asset={asset} env={env} />
            ))}
            {rows.length === 0 ? (
              <div className="p-5">
                <NoMatch />
              </div>
            ) : null}
          </div>
        </div>
        <div className="flex min-h-11 shrink-0 items-center gap-4 border-t border-line px-5 py-2 text-[13px] text-muted-foreground">
          <span>
            Showing {rows.length} of {items.length}
            {next_cursor ? ' (more available)' : ''}
          </span>
          <SlaLegend className="ml-auto" />
          <span>SLA history unavailable</span>
        </div>
      </div>
    </>
  )
}

function DesktopRow({ asset, env }: { asset: ApiAsset; env: Env }) {
  const layer = layerOf(asset)
  return (
    <div
      role="row"
      className={cn(grid, 'h-[46px] border-b border-line-soft hover:bg-raised')}
    >
      <Link
        to="/assets/$assetId"
        params={{ assetId: asset.id }}
        search={{ env }}
        className="truncate font-mono text-[13px] text-foreground hover:text-link"
      >
        {asset.id}
      </Link>
      <span>
        {layer ? (
          <>
            <LayerSwatch layer={layer} />{' '}
            <span className="capitalize">{layer}</span>
          </>
        ) : (
          'Unknown'
        )}
      </span>
      <span className="text-[13px] text-muted-foreground">Unknown</span>
      <span className="text-[13px] text-muted-foreground">
        {asset.last_materialization_at ?? 'Not observed'}
      </span>
      <span className="text-right text-muted-foreground">—</span>
      <span className="text-right text-muted-foreground">—</span>
      <span className="truncate text-[13px] text-muted-foreground">
        Unavailable
      </span>
      <span className="text-[13px] text-muted-foreground">Unavailable</span>
    </div>
  )
}

function MobileRow({ asset, env }: { asset: ApiAsset; env: Env }) {
  const layer = layerOf(asset)
  return (
    <Link
      to="/assets/$assetId"
      params={{ assetId: asset.id }}
      search={{ env }}
      className="flex min-h-[60px] items-center gap-3 border-b border-line-soft px-3.5 py-2.5 text-foreground last:border-b-0 hover:bg-raised"
    >
      <span className="size-2.5 shrink-0 rounded-full bg-border-strong" />
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="truncate font-mono text-[13px]">{asset.id}</span>
        <span className="flex items-center gap-1.5 overflow-hidden text-[12.5px] whitespace-nowrap text-muted-foreground">
          {layer ? <LayerSwatch layer={layer} /> : null}
          <span className="capitalize">{layer ?? 'Unknown layer'}</span>
          <span className="truncate">{asset.group_name ?? 'No group'}</span>
        </span>
      </span>
      <span className="shrink-0 text-xs whitespace-nowrap text-muted-foreground">
        Freshness unknown
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
