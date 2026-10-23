/** Lists actual draft product declarations, never table aliases or simulated shares. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { PlusIcon, SearchIcon } from 'lucide-react'
import { z } from 'zod'
import { getDataProducts } from '@/lib/data/api/data-products'
import { NewProduct } from '@/components/data-products/new-product'
import { PageHeader } from '@/components/phlo/page'
import { ActionButton as Button } from '@/components/phlo/action-button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'

export const Route = createFileRoute('/_app/data-products/')({
  validateSearch: z.object({ archived: z.boolean().default(false) }),
  loaderDeps: ({ search }) => ({
    env: search.env,
    include_archived: search.archived,
  }),
  loader: ({ deps }) => getDataProducts({ data: deps }),
  head: () => ({ meta: [{ title: 'Data products · phlo' }] }),
  component: ProductsPage,
})
function ProductsPage() {
  const data = Route.useLoaderData()
  const navigate = Route.useNavigate()
  const { archived } = Route.useSearch()
  const router = useRouter()
  const [creating, setCreating] = React.useState(false)
  const [query, setQuery] = React.useState('')
  const filtered = data.items.filter((item) =>
    [
      item.name,
      item.purpose,
      item.owner,
      ...item.classifications,
      ...item.member_dataset_ids,
    ]
      .join(' ')
      .toLowerCase()
      .includes(query.toLowerCase()),
  )
  return (
    <>
      <PageHeader
        title="Data products"
        meta="Curated sets of Datasets with an owner and purpose"
        actions={
          <Button
            operation="v1_data_product_create"
            onClick={() => setCreating(true)}
          >
            <PlusIcon />
            New data product
          </Button>
        }
      />
      <div className="flex shrink-0 flex-wrap items-center gap-4 border-b border-line px-5 py-3 text-[13px]">
        <span className="rounded-md bg-soft px-3 py-1.5">
          All · {data.items.length}
        </span>
        <span className="text-muted-foreground">
          Draft declarations · {data.env}
        </span>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={archived}
            onChange={(event) =>
              void navigate({
                search: (previous) => ({
                  ...previous,
                  archived: event.target.checked,
                }),
              })
            }
          />
          Include archived definitions
        </label>
      </div>
      <div className="grid min-h-0 flex-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4 p-5">
          {creating ? (
            <NewProduct
              env={data.env}
              assets={data.assets.items}
              onCancel={() => setCreating(false)}
              onCreated={(product) => {
                void router.invalidate()
                void navigate({
                  to: '/data-products/$productId',
                  params: { productId: product.product_id },
                  search: { env: data.env },
                })
              }}
            />
          ) : null}
          <label className="flex items-center gap-2">
            <SearchIcon className="size-4 text-muted-foreground" />
            <span className="sr-only">Find a product, Dataset or owner</span>
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find a product, Dataset or owner"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((product) => (
              <Link
                key={product.product_id}
                to="/data-products/$productId"
                params={{ productId: product.product_id }}
                search={{ env: data.env }}
                className="flex min-h-40 flex-col gap-2.5 rounded-xl border border-line bg-card p-4 text-foreground hover:border-border-strong"
              >
                <div className="flex items-center justify-between gap-2">
                  <Badge variant="outline">
                    {product.status === 'archived' ? 'Archived' : 'Draft'}
                  </Badge>
                  <Badge variant="branch">
                    {product.classifications.join(', ')}
                  </Badge>
                </div>
                <h2 className="text-[15px] font-medium">{product.name}</h2>
                <p className="line-clamp-3 text-[13px] leading-relaxed text-muted-foreground">
                  {product.purpose}
                </p>
                <div className="mt-auto flex flex-wrap items-center justify-between gap-1 text-xs text-muted-foreground">
                  <span>
                    {product.member_dataset_ids.length} Datasets · v
                    {product.version}
                  </span>
                  <span>{product.owner}</span>
                </div>
              </Link>
            ))}
          </div>
          {!filtered.length ? (
            <p className="rounded-xl border border-line p-6 text-sm text-muted-foreground">
              {data.items.length
                ? 'No products match this search.'
                : 'No data products declared in this environment. Create a draft collection from the actual asset inventory.'}
            </p>
          ) : null}
          {data.assets.next_cursor ? (
            <p className="text-xs text-muted-foreground">
              The new-product picker is limited to the first 100 asset records.
            </p>
          ) : null}
        </div>
        <aside className="space-y-4 border-t border-line bg-raised p-5 text-[13px] lg:border-t-0 lg:border-l">
          <h2 className="text-[15px] font-medium">Access requests</h2>
          <p className="text-muted-foreground">
            Client sharing and access requests are not available for draft
            product declarations. No access is granted by adding a Dataset.
          </p>
          <h3 className="border-t border-line pt-4 font-medium">
            Canonical governance
          </h3>
          <p className="text-muted-foreground">
            Each member keeps its own Dataset identity, policy verdict and
            publication state. Open a product to inspect missing declarations
            and blockers.
          </p>
          <Link to="/datasets" search={{ env: data.env }}>
            Inspect governed Datasets
          </Link>
        </aside>
      </div>
    </>
  )
}
