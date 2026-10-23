/** Draft collections reference canonical Dataset IDs without publishing or granting access. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'
import { getAssetList } from './assets'

const canonicalSchema = z
  .object({
    dataset_id: z.string(),
    table_id: z.string(),
    declared: z.boolean(),
    owner: z.string().nullable(),
    classifications: z.array(z.string()),
    publication_state: z.string().nullable(),
    readiness: z
      .object({
        ready: z.boolean(),
        action: z.string(),
        reasons: z.array(z.string()),
      })
      .catchall(z.json()),
  })
  .catchall(z.json())
const productSchema = z.object({
  product_id: z.string(),
  env: environmentSchema,
  name: z.string(),
  purpose: z.string(),
  owner: z.string(),
  classifications: z.array(z.string()),
  member_dataset_ids: z.array(z.string()),
  version: z.number().int().positive(),
  status: z.enum(['draft', 'archived']),
  created_at: z.string(),
  updated_at: z.string(),
  members: z.array(
    z.object({
      dataset_id: z.string(),
      canonical: canonicalSchema.nullable(),
      reason: z.string().nullable(),
    }),
  ),
  sharing_status: z.literal('unavailable'),
  sharing_reason: z.string(),
})
export type DataProduct = z.infer<typeof productSchema>
export const productInputSchema = z
  .object({
    env: environmentSchema,
    product_id: z.string().regex(/^[a-z][a-z0-9_-]{0,62}$/),
    name: z.string().trim().min(1).max(160),
    purpose: z.string().trim().min(1).max(4000),
    owner: z.string().trim().min(1).max(160),
    classifications: z.array(z.string().trim().min(1).max(160)).min(1).max(10),
    member_dataset_ids: z
      .array(z.string().min(1))
      .min(1)
      .max(50)
      .refine(
        (items) => new Set(items).size === items.length,
        'Choose each Dataset once.',
      ),
  })
  .strict()
export const getDataProducts = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      include_archived: z.boolean().optional(),
    }),
  )
  .handler(async ({ data: { env, include_archived } }) => {
    const [products, assets] = await Promise.all([
      phloApi(
        `api/v1/data-products?env=${encodeURIComponent(env)}${include_archived ? '&include_archived=true' : ''}`,
        z.object({ env: environmentSchema, items: z.array(productSchema) }),
        { env },
      ),
      getAssetList({ data: { env } }),
    ])
    return { ...products, assets }
  })
export const getDataProduct = createServerFn({ method: 'GET' })
  .inputValidator(z.object({ env: environmentSchema, product_id: z.string() }))
  .handler(async ({ data }) => {
    const [product, assets] = await Promise.all([
      phloApi(
        `api/v1/data-products/${encodeURIComponent(data.product_id)}?env=${encodeURIComponent(data.env)}`,
        productSchema,
        { env: data.env },
      ),
      getAssetList({ data: { env: data.env } }),
    ])
    return { product, assets }
  })
export const createDataProduct = createServerFn({ method: 'POST' })
  .inputValidator(productInputSchema)
  .handler(({ data: { env, ...data } }) =>
    phloApi(
      `api/v1/data-products?env=${encodeURIComponent(env)}`,
      productSchema,
      { env, method: 'POST', body: { ...data, expected_version: 0 } },
    ),
  )
export const updateDataProduct = createServerFn({ method: 'POST' })
  .inputValidator(
    productInputSchema.extend({
      expected_version: z.number().int().positive(),
    }),
  )
  .handler(({ data: { env, ...data } }) =>
    phloApi(
      `api/v1/data-products/${encodeURIComponent(data.product_id)}?env=${encodeURIComponent(env)}`,
      productSchema,
      { env, method: 'PUT', body: data },
    ),
  )

export const changeProductLifecycle = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      id: z.string().min(1),
      action: z.enum(['archive', 'restore']),
      expected_version: z.number().int().positive(),
    }),
  )
  .handler(({ data: { env, id, action, expected_version } }) =>
    phloApi(
      `api/v1/data-products/${encodeURIComponent(id)}/${action === 'archive' ? 'archive' : 'restore'}?env=${env}`,
      productSchema,
      { env, method: 'POST', body: { expected_version } },
    ),
  )

export const exportProductDraft = createServerFn({ method: 'GET' })
  .inputValidator(z.object({ env: environmentSchema, id: z.string().min(1) }))
  .handler(({ data: { env, id } }) =>
    phloApi(
      `api/v1/data-products/${encodeURIComponent(id)}/export?env=${env}`,
      z.object({
        format: z.literal('phlo.product-draft'),
        schema_version: z.literal(1),
        env: environmentSchema,
        version: z.number().int().positive(),
        status: z.enum(['draft', 'archived']),
        definition: productInputSchema.omit({ env: true }),
      }),
      { env },
    ),
  )
export function productQuery(table: string) {
  return `SELECT * FROM ${table
    .split('.')
    .map((part) => `"${part.replaceAll('"', '""')}"`)
    .join('.')} LIMIT 100`
}
