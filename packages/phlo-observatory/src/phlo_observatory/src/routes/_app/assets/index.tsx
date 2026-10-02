/** Defines the searchable, filterable asset catalogue route. */
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { z } from 'zod'
import { getAssetList } from '@/lib/data/api/assets'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/assets/')({
  validateSearch: z.object({
    q: z.string().default(''),
    group: z.string().optional(),
    filter: z.enum(['all', 'attention']).optional(),
    layer: z.enum(['bronze', 'silver', 'gold']).optional(),
  }),
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getAssetList({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Assets · phlo' }] }),
  component: AssetsPage,
})

function AssetsPage() {
  const { items, env, next_cursor } = Route.useLoaderData()
  const { q, group, filter } = Route.useSearch()
  const navigate = Route.useNavigate()
  const router = useRouter()
  const groups = [
    ...new Set(items.map((a) => a.group_name).filter((name) => name !== null)),
  ].sort()
  const rows = items.filter(
    (a) =>
      (!group || a.group_name === group) &&
      a.id.toLowerCase().includes(q.trim().toLowerCase()),
  )
  return (
    <>
      <PageHeader
        title="Assets"
        meta={`${items.length} assets · ${env}`}
        actions={
          <Button variant="outline" onClick={() => void router.invalidate()}>
            Refresh
          </Button>
        }
      />
      {filter === 'attention' ? (
        <div
          role="note"
          className="border-b border-warn bg-warn-wash px-4 py-2 text-sm text-warn-ink"
        >
          The freshness filter is unavailable. This view shows all matching
          assets, not only assets needing attention.
        </div>
      ) : null}
      <div className="flex shrink-0 flex-col gap-2.5 border-b border-line px-4 py-3 lg:px-5 lg:py-3.5">
        <label className="flex max-w-md flex-col gap-1.5 text-[13.5px]">
          Find an asset
          <Input
            type="search"
            value={q}
            onChange={(e) =>
              void navigate({
                search: (p) => ({ ...p, q: e.target.value }),
                replace: true,
              })
            }
            placeholder="Filter by asset name"
          />
        </label>
        <div
          role="group"
          aria-label="Filter assets"
          className="flex gap-2 overflow-x-auto"
        >
          {[
            { name: 'All', value: undefined },
            ...groups.map((name) => ({ name, value: name })),
          ].map((chip) => (
            <button
              key={chip.name}
              type="button"
              aria-pressed={group === chip.value}
              onClick={() =>
                void navigate({ search: (p) => ({ ...p, group: chip.value }) })
              }
              className={cn(
                'h-10 shrink-0 rounded-full border px-3.5 text-[13px] lg:h-[30px]',
                group === chip.value
                  ? 'border-foreground bg-foreground text-background'
                  : 'border-border bg-card text-text-2 hover:bg-soft',
              )}
            >
              {chip.name} ·{' '}
              {chip.value
                ? items.filter((a) => a.group_name === chip.value).length
                : items.length}
            </button>
          ))}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-auto">
        <div className="flex flex-col gap-2.5 p-4 md:hidden">
          <Eyebrow>
            Showing {rows.length} of {items.length}
          </Eyebrow>
          <Card className="overflow-hidden">
            {rows.map((a) => (
              <Link
                key={a.id}
                to="/assets/$assetId"
                params={{ assetId: a.id }}
                search={{ env }}
                className="flex flex-col gap-1 border-b border-line-soft px-3.5 py-3 text-foreground last:border-b-0 hover:bg-raised"
              >
                <span className="break-all font-mono text-[13px]">{a.id}</span>
                <span className="text-xs text-muted-foreground">
                  {a.group_name ?? 'No group'} ·{' '}
                  {a.last_materialization_at
                    ? 'Materialized'
                    : 'No materialization observed'}
                </span>
              </Link>
            ))}
          </Card>
        </div>
        <div className="hidden min-w-[1040px] md:block">
          <Table aria-label="Assets">
            <TableHeader>
              <TableRow>
                {[
                  'Asset',
                  'Group',
                  'Freshness',
                  'Last materialized',
                  'Rows',
                  'Size',
                  'Owner',
                  'Last 7 days',
                ].map((label) => (
                  <TableHead key={label}>{label}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>
                    <Link
                      to="/assets/$assetId"
                      params={{ assetId: a.id }}
                      search={{ env }}
                      className="font-mono text-[13px] text-foreground hover:text-link"
                    >
                      {a.id}
                    </Link>
                  </TableCell>
                  <TableCell>{a.group_name ?? 'Unknown'}</TableCell>
                  <TableCell className="text-muted-foreground">
                    Unknown
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {a.last_materialization_at ?? 'Not observed'}
                  </TableCell>
                  {['Rows', 'Size', 'Owner', 'History'].map((label) => (
                    <TableCell
                      key={label}
                      className="text-muted-foreground"
                      title={`${label} is not supplied by the asset inventory API.`}
                    >
                      Unavailable
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {!rows.length ? (
          <div className="p-5">
            <EmptyState
              title={
                items.length
                  ? 'No assets match'
                  : 'No assets in this environment'
              }
            >
              Try another name or group. An empty inventory does not imply
              healthy data.
            </EmptyState>
          </div>
        ) : null}
      </div>
      <div className="shrink-0 border-t border-line px-5 py-3 text-[13px] text-muted-foreground">
        Showing {rows.length} of {items.length}
        {next_cursor ? ' · More assets are available from the API.' : ''}.
        Freshness, ownership, size, and SLA history are not supplied by this
        inventory.
      </div>
    </>
  )
}
