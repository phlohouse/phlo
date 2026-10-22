/** Review a draft collection against the actual selected-environment asset inventory. */
import * as React from 'react'
import type { ApiAsset } from '@/lib/data/api/assets'
import type { DataProduct } from '@/lib/data/api/data-products'
import {
  createDataProduct,
  productInputSchema,
} from '@/lib/data/api/data-products'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

export function NewProduct({
  env,
  assets,
  onCreated,
  onCancel,
}: {
  env: string
  assets: Array<ApiAsset>
  onCreated: (product: DataProduct) => void
  onCancel: () => void
}) {
  const [review, setReview] = React.useState<ReturnType<
    typeof productInputSchema.parse
  > | null>(null)
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const relations = [
    ...new Set(
      assets
        .map((asset) => asset.relation)
        .filter((relation): relation is string => Boolean(relation)),
    ),
  ]
  async function save() {
    if (!review) return
    setBusy(true)
    setError('')
    try {
      onCreated(await createDataProduct({ data: review }))
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Could not create the draft. Reload before retrying.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <section
      className="rounded-xl border border-line bg-card p-5"
      aria-labelledby="new-product-title"
    >
      <h2 id="new-product-title" className="mb-3 text-[15px] font-medium">
        New data product
      </h2>
      <p className="mb-4 text-sm text-muted-foreground">
        Declare a curated set of real tables with an owner and purpose. It
        remains a draft and grants no access.
      </p>
      {review ? (
        <div className="space-y-3 text-sm">
          <h3 className="font-medium">Review draft collection</h3>
          <p>
            {review.name} · {review.owner}
          </p>
          <p>{review.purpose}</p>
          <p>Classification: {review.classifications.join(', ')}</p>
          <ul className="list-disc pl-5">
            {review.member_dataset_ids.map((id) => (
              <li key={id} className="font-mono text-xs">
                {id}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground">
            Member governance, readiness and publication stay under canonical
            Dataset authority. This creates only the collection declaration.
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setReview(null)}
            >
              Edit
            </Button>
            <Button disabled={busy} onClick={() => void save()}>
              {busy ? 'Creating draft…' : 'Create draft product'}
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault()
            const form = new FormData(event.currentTarget)
            const parsed = productInputSchema.safeParse({
              env,
              product_id: form.get('product_id'),
              name: form.get('name'),
              purpose: form.get('purpose'),
              owner: form.get('owner'),
              classifications: String(form.get('classification'))
                .split(',')
                .map((item) => item.trim())
                .filter(Boolean),
              member_dataset_ids: form.getAll('member'),
            })
            if (parsed.success) {
              setError('')
              setReview(parsed.data)
            } else
              setError(
                parsed.error.issues
                  .map((issue) => `${issue.path.join('.')}: ${issue.message}`)
                  .join(' '),
              )
          }}
        >
          {[
            {
              name: 'name',
              label: 'Product name',
              placeholder: 'Batch record data · CGT-04',
            },
            {
              name: 'product_id',
              label: 'Product ID',
              placeholder: 'batch-record-cgt04',
            },
            { name: 'owner', label: 'Owner', placeholder: 'QC Analytics' },
            {
              name: 'classification',
              label: 'Classification',
              placeholder: 'Internal',
            },
          ].map((field) => (
            <label key={field.name} className="flex flex-col gap-1.5 text-sm">
              <span>{field.label}</span>
              <Input
                name={field.name}
                placeholder={field.placeholder}
                required
              />
            </label>
          ))}
          <label className="flex flex-col gap-1.5 text-sm sm:col-span-2">
            <span>Purpose and scope</span>
            <textarea
              name="purpose"
              required
              className="min-h-20 rounded-lg border border-border bg-card p-3"
            />
          </label>
          <fieldset className="space-y-2 rounded-lg border border-line p-3 sm:col-span-2">
            <legend className="px-1 text-sm">Included Datasets</legend>
            {relations.length ? (
              relations.map((relation) => (
                <label
                  key={relation}
                  className="flex items-center gap-2 font-mono text-xs"
                >
                  <input type="checkbox" name="member" value={relation} />
                  {relation}
                </label>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                No asset relations are available in this environment.
              </p>
            )}
          </fieldset>
          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" disabled={!relations.length}>
              Review draft
            </Button>
            <Button variant="outline" type="button" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {error ? (
        <p role="alert" className="mt-3 text-sm text-bad-text">
          {error}
        </p>
      ) : null}
    </section>
  )
}
