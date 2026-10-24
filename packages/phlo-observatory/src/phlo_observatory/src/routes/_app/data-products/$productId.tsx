/** Shows collection metadata alongside real inventory and canonical member governance. */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Share2Icon } from 'lucide-react'
import type { DataProduct } from '@/lib/data/api/data-products'
import type { ApiAsset } from '@/lib/data/api/assets'
import { getDataProduct, productQuery } from '@/lib/data/api/data-products'
import { formatInventoryCount } from '@/lib/data/assets-table'
import { PageHeader } from '@/components/phlo/page'
import { Button, buttonVariants } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

export const Route = createFileRoute('/_app/data-products/$productId')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps, params }) =>
    getDataProduct({ data: { env: deps.env, product_id: params.productId } }),
  head: () => ({ meta: [{ title: 'Data product · phlo' }] }),
  component: ProductPage,
})
function ProductPage() {
  const { product, assets } = Route.useLoaderData()
  const first = product.member_dataset_ids[0]
  const ready = product.members.filter(
    (member) => member.canonical?.readiness.ready,
  ).length
  return (
    <>
      <PageHeader
        crumbs={[{ label: 'Data products', to: '/data-products' }]}
        title={product.name}
        meta={`Draft · v${product.version}`}
        actions={
          <>
            {first ? (
              <Link
                to="/query"
                search={{ env: product.env, sql: productQuery(first) }}
                className={buttonVariants({ variant: 'outline' })}
              >
                Query it
              </Link>
            ) : null}
            <Button variant="outline" disabled title={product.sharing_reason}>
              <Share2Icon />
              Share with a client
            </Button>
          </>
        }
      />
      <div className="flex shrink-0 gap-5 border-b border-line px-5 py-3 text-[13px]">
        <span className="font-medium">Overview</span>
        <span className="text-muted-foreground">
          Draft declaration · no access grants
        </span>
      </div>
      <div className="grid min-h-0 flex-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-6 p-5">
          <p className="max-w-[75ch] text-sm leading-relaxed">
            {product.purpose}
          </p>
          <section aria-labelledby="product-review-title">
            <h2
              id="product-review-title"
              className="mb-2.5 text-xs tracking-wide text-muted-foreground uppercase"
            >
              Declaration and review
            </h2>
            <div className="grid grid-cols-2 gap-2.5 xl:grid-cols-4">
              {[
                [
                  'Collection state',
                  'Draft',
                  'No product publication recorded',
                ],
                [
                  'Member readiness',
                  `${ready} of ${product.members.length}`,
                  'Canonical Dataset verdicts',
                ],
                [
                  'Included Datasets',
                  String(product.members.length),
                  'Membership grants no access',
                ],
                [
                  'Version',
                  String(product.version),
                  `Updated ${new Date(product.updated_at).toLocaleDateString('en-GB')}`,
                ],
              ].map(([label, value, sub]) => (
                <div
                  key={label}
                  className="space-y-1.5 rounded-xl border border-line p-3"
                >
                  <div className="text-xs text-muted-foreground">{label}</div>
                  <div className="text-[15px] font-medium">{value}</div>
                  <div className="text-xs text-muted-foreground">{sub}</div>
                </div>
              ))}
            </div>
          </section>
          <section aria-labelledby="product-members-title">
            <h2
              id="product-members-title"
              className="mb-2.5 text-xs tracking-wide text-muted-foreground uppercase"
            >
              What's in it · {product.members.length} Datasets
            </h2>
            <div className="divide-y divide-line rounded-xl border border-line">
              {product.members.map((member) => (
                <ProductMember
                  key={member.dataset_id}
                  member={member}
                  env={product.env}
                  asset={assets.items.find(
                    (item) => item.relation === member.dataset_id,
                  )}
                />
              ))}
            </div>
          </section>
          <section>
            <h2 className="mb-2 text-xs tracking-wide text-muted-foreground uppercase">
              Who reads it
            </h2>
            <p className="text-[13px] text-muted-foreground">
              Product-level usage and client shares are not recorded. Existing
              Dataset grants are separate from this collection declaration.
            </p>
          </section>
        </div>
        <aside className="space-y-4 border-t border-line bg-raised p-5 text-[13px] lg:border-t-0 lg:border-l">
          <h2 className="text-[15px] font-medium">Details</h2>
          <dl className="grid grid-cols-[95px_1fr] gap-3">
            <dt className="text-muted-foreground">Owner</dt>
            <dd>{product.owner}</dd>
            <dt className="text-muted-foreground">Classification</dt>
            <dd>{product.classifications.join(', ')}</dd>
            <dt className="text-muted-foreground">Environment</dt>
            <dd>{product.env}</dd>
            <dt className="text-muted-foreground">Version</dt>
            <dd>{product.version}</dd>
            <dt className="text-muted-foreground">Created</dt>
            <dd>{new Date(product.created_at).toLocaleString('en-GB')}</dd>
          </dl>
          <h3 className="border-t border-line pt-4 font-medium">
            Publication and sharing
          </h3>
          <p className="text-muted-foreground">{product.sharing_reason}</p>
          <p className="text-muted-foreground">
            Classification and ownership here are declared collection metadata.
            They do not override member policies, grant access or attest that a
            batch was released.
          </p>
          <Link to="/datasets" search={{ env: product.env }}>
            Inspect canonical Dataset authority
          </Link>
        </aside>
      </div>
    </>
  )
}

function ProductMember({
  member,
  asset,
  env,
}: {
  member: DataProduct['members'][number]
  asset: ApiAsset | undefined
  env: string
}) {
  return (
    <article className="space-y-2 p-3.5">
      <MemberAssetHeader
        asset={asset}
        datasetId={member.dataset_id}
        env={env}
      />
      <p className="text-xs text-muted-foreground">
        {asset?.description ?? 'No asset description available.'}
      </p>
      <MemberGovernance member={member} />
    </article>
  )
}
function MemberAssetHeader({
  asset,
  datasetId,
  env,
}: {
  asset: ApiAsset | undefined
  datasetId: string
  env: string
}) {
  const freshness = asset?.freshness_status ?? 'unknown'
  const tones = { fresh: 'ok', stale: 'bad', unknown: 'neutral' } as const
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      {asset ? (
        <Link
          to="/assets/$assetId"
          params={{ assetId: asset.id }}
          search={{ env }}
          className="break-all font-mono text-[13px]"
        >
          {datasetId}
        </Link>
      ) : (
        <code className="text-[13px]">{datasetId}</code>
      )}
      <span className="text-xs text-muted-foreground">
        {asset?.row_count == null
          ? 'Rows not observed'
          : `${formatInventoryCount(asset.row_count)} rows`}
      </span>
      <Badge variant={tones[freshness]}>
        {freshness === 'unknown' ? 'Freshness unknown' : freshness}
      </Badge>
    </div>
  )
}
function MemberGovernance({
  member,
}: {
  member: DataProduct['members'][number]
}) {
  const canonical = member.canonical
  return (
    <>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          Governance: {canonical?.declared ? 'Declared' : 'Incomplete'}
        </span>
        <span>
          Publication: {canonical?.publication_state ?? 'Not recorded'}
        </span>
        <span>
          Readiness: {canonical?.readiness.ready ? 'Ready' : 'Blocked'}
        </span>
      </div>
      {member.reason ? (
        <p className="text-xs text-warn-ink">{member.reason}</p>
      ) : null}
      {canonical?.readiness.reasons.length ? (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">
            Canonical readiness reasons
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-4">
            {canonical.readiness.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  )
}
