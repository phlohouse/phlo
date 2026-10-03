/** Loads asset metadata and tab evidence while preserving guarded mutations. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { CodeIcon, PlusIcon } from 'lucide-react'
import { z } from 'zod'
import type { Layer } from '@/lib/data/types'
import {
  assetLayer,
  assetTabSchema,
  getAssetDetail,
} from '@/lib/data/api/assets'
import { PageHeader } from '@/components/phlo/page'
import { LayerSwatch, Mono } from '@/components/phlo/status'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  AuditsTab,
  DataTab,
  LineageTab,
  OverviewTab,
  SchemaTab,
  SnapshotsTab,
  UnavailableTab,
} from '@/components/assets/asset-tabs'
import { MaterializeDialog } from '@/components/assets/materialize-dialog'
import { BackfillDialog } from '@/components/assets/backfill-dialog'
import { AddAuditDialog } from '@/components/assets/add-audit-dialog'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/assets/$assetId')({
  validateSearch: z.object({
    tab: assetTabSchema.default('overview'),
    dialog: z.enum(['materialize', 'backfill', 'audit']).optional(),
  }),
  loaderDeps: ({ search }) => ({ env: search.env, tab: search.tab }),
  loader: ({ params, deps }) =>
    getAssetDetail({ data: { id: params.assetId, ...deps } }),
  head: ({ params }) => ({ meta: [{ title: `${params.assetId} · phlo` }] }),
  component: AssetPage,
})

const labels = {
  overview: 'Overview',
  data: 'Data',
  schema: 'Schema history',
  lineage: 'Lineage',
  snapshots: 'Snapshots',
  audits: 'Audits',
}
const layerSoft: Record<Layer, string> = {
  bronze: 'bg-bronze-soft',
  silver: 'bg-silver-soft',
  gold: 'bg-gold-soft',
}

function AssetPage() {
  const result = Route.useLoaderData()
  const { asset, jobs, env } = result
  const { tab, dialog } = Route.useSearch()
  const navigate = Route.useNavigate()
  const router = useRouter()
  const tabs = React.useRef<HTMLDivElement>(null)
  React.useEffect(() => {
    tabs.current
      ?.querySelector('[data-active]')
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [tab])
  const layer = assetLayer(asset)
  const close = () =>
    void navigate({
      search: (previous) => ({ ...previous, dialog: undefined }),
      replace: true,
    })
  const auditButton = (
    <Button
      variant="outline"
      disabled={!asset.columns.length}
      onClick={() =>
        void navigate({
          search: (previous) => ({ ...previous, dialog: 'audit' }),
        })
      }
    >
      <PlusIcon /> Add audit
    </Button>
  )

  return (
    <>
      <PageHeader
        crumbs={[{ label: 'Assets', to: '/assets' }]}
        title={<Mono className="text-[13.5px]">{asset.id}</Mono>}
        actions={
          <>
            <Link
              to="/query"
              search={{ env }}
              className={buttonVariants({ variant: 'outline' })}
            >
              <CodeIcon /> Query
            </Link>
            <Button
              disabled={asset.is_source || !jobs.length}
              onClick={() =>
                void navigate({
                  search: (previous) => ({
                    ...previous,
                    dialog: 'materialize',
                  }),
                })
              }
            >
              Materialize
            </Button>
            <Button
              variant="outline"
              disabled={asset.is_source || !jobs.length}
              onClick={() =>
                void navigate({
                  search: (previous) => ({ ...previous, dialog: 'backfill' }),
                })
              }
            >
              Backfill
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <Tabs
          value={tab}
          onValueChange={(value) =>
            void navigate({
              search: (previous) => ({
                ...previous,
                tab: assetTabSchema.parse(value),
              }),
              replace: true,
            })
          }
        >
          <div className="flex flex-col gap-2.5 border-b border-line px-4 pt-5 lg:px-7 lg:pt-[22px]">
            <div className="flex flex-wrap items-center gap-2">
              {layer ? (
                <Badge
                  size="lg"
                  className={cn('text-text-2', layerSoft[layer])}
                >
                  <LayerSwatch layer={layer} />
                  <span className="capitalize">{layer}</span>
                </Badge>
              ) : (
                <Badge variant="neutral" size="lg">
                  Layer unknown
                </Badge>
              )}
              <Badge variant="neutral" size="lg">
                Freshness unknown
              </Badge>
              <Badge variant="outline" size="lg">
                {asset.compute_kind ?? 'Compute kind unknown'}
              </Badge>
              {asset.is_source ? (
                <Badge variant="outline" size="lg">
                  Source
                </Badge>
              ) : null}
            </div>
            <h1 className="m-0 font-mono text-lg font-medium tracking-[-0.01em] [overflow-wrap:anywhere] lg:text-[22px]">
              {asset.id}
            </h1>
            <p className="m-0 max-w-[900px] text-sm leading-normal text-text-3">
              {asset.description ?? 'No description supplied.'}
            </p>
            <TabsList
              ref={tabs}
              aria-label="Asset views"
              className="-mx-4 overflow-x-auto border-b-0 px-4 pt-2 pb-3 [scrollbar-width:none] lg:mx-0 lg:px-0"
            >
              {assetTabSchema.options.map((value) => (
                <TabsTrigger
                  key={value}
                  value={value}
                  className="shrink-0 whitespace-nowrap"
                >
                  {labels[value]}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <TabsContent value={tab}>
            {result.kind === 'unavailable' ? (
              <UnavailableTab
                message={result.message}
                action={
                  <Button
                    variant="outline"
                    onClick={() => void router.invalidate()}
                  >
                    Try again
                  </Button>
                }
              />
            ) : null}
            {result.kind === 'overview' ? (
              <OverviewTab asset={asset} env={env} jobs={jobs} />
            ) : null}
            {result.kind === 'data' ? (
              <DataTab
                key={`${env}:${asset.id}:${result.data.sql}`}
                asset={asset}
                data={result.data}
              />
            ) : null}
            {result.kind === 'schema' ? <SchemaTab data={result.data} /> : null}
            {result.kind === 'lineage' ? (
              <LineageTab asset={asset} env={env} />
            ) : null}
            {result.kind === 'snapshots' ? (
              <SnapshotsTab
                key={`${env}:${asset.id}:${result.data.metadata_location}`}
                data={result.data}
                onRolledBack={() => void router.invalidate()}
              />
            ) : null}
            {result.kind === 'audits' ? (
              <AuditsTab data={result.data} addAudit={auditButton} />
            ) : null}
          </TabsContent>
        </Tabs>
      </div>
      <MaterializeDialog
        key={`${env}:${asset.id}`}
        open={dialog === 'materialize'}
        onClose={close}
        assetId={asset.id}
        env={env}
        jobs={jobs.map((job) => job.id)}
      />
      <BackfillDialog
        key={`backfill:${env}:${asset.id}`}
        open={dialog === 'backfill'}
        onClose={close}
        assetId={asset.id}
        env={env}
        jobs={jobs.map((job) => job.id)}
      />
      <AddAuditDialog
        key={`audit:${env}:${asset.id}`}
        open={dialog === 'audit'}
        onClose={close}
        assetId={asset.id}
        env={env}
        columns={asset.columns}
      />
    </>
  )
}
