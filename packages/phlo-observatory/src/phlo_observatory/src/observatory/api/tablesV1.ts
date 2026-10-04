/** Read-only, environment-scoped v1 asset contracts for the Tables screen. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'

import type { ObservatoryEnvironment } from './environment'
import { apiGet } from '@/server/phlo-api'

const environmentSchema = z.enum(['prod', 'staging'])
const assetSchema = z.object({
  id: z.string().min(1),
  key: z.array(z.string().min(1)).min(1),
  description: z.string().nullable(),
  compute_kind: z.string().nullable(),
  group_name: z.string().nullable(),
  is_source: z.boolean(),
  dependencies: z.array(z.array(z.string())),
  last_materialization_at: z.string().nullable(),
  last_run_id: z.string().nullable(),
  relation: z.string().nullable(),
  history_scoped: z.boolean(),
})
const assetPageSchema = z.object({
  env: environmentSchema,
  items: z.array(assetSchema),
  next_cursor: z.string().nullable(),
})
const assetDetailSchema = assetSchema.extend({
  columns: z.array(
    z.object({
      name: z.string().min(1),
      type: z.string().nullable(),
      description: z.string().nullable(),
    }),
  ),
  schema_observed_at: z.string().nullable(),
  column_lineage: z
    .record(
      z.string(),
      z.array(
        z.object({
          asset_key: z.array(z.string()),
          column_name: z.string(),
        }),
      ),
    )
    .nullable(),
})
const previewSchema = z.object({
  env: environmentSchema,
  asset_id: z.string().min(1),
  nessie_ref: z.string().min(1),
  columns: z.array(
    z.object({ name: z.string().min(1), type: z.string().nullable() }),
  ),
  rows: z.array(z.record(z.string(), z.json())),
  has_more: z.boolean(),
})

export type V1Asset = z.infer<typeof assetSchema>
export type V1AssetDetail = z.infer<typeof assetDetailSchema>
export type V1AssetPreview = z.infer<typeof previewSchema>
export type V1TablesRead<T> =
  | { kind: 'available'; data: T }
  | { kind: 'unavailable'; message: string }

function unavailable<T>(error: unknown): V1TablesRead<T> {
  return {
    kind: 'unavailable',
    message:
      error instanceof Error ? error.message : 'Table data is unavailable.',
  }
}

export async function getV1AssetsPageData({
  environment,
  cursor,
}: {
  environment: ObservatoryEnvironment
  cursor: string | null
}): Promise<V1TablesRead<z.infer<typeof assetPageSchema>>> {
  try {
    const data = assetPageSchema.parse(
      await apiGet('/api/v1/assets', {
        env: environment,
        limit: 100,
        ...(cursor ? { cursor } : {}),
      }),
    )
    if (data.env !== environment) {
      throw new Error('phlo-api returned assets for another environment.')
    }
    return { kind: 'available', data }
  } catch (error) {
    return unavailable(error)
  }
}

export const getV1AssetsPage = createServerFn()
  .inputValidator(
    z.object({ environment: environmentSchema, cursor: z.string().nullable() }),
  )
  .handler(({ data }) => getV1AssetsPageData(data))

export async function getV1AssetDetailData({
  environment,
  assetId,
}: {
  environment: ObservatoryEnvironment
  assetId: string
}): Promise<V1TablesRead<V1AssetDetail>> {
  try {
    const detail = assetDetailSchema.parse(
      await apiGet(`/api/v1/assets/${encodeURIComponent(assetId)}`, {
        env: environment,
      }),
    )
    if (detail.id !== assetId) {
      throw new Error('phlo-api returned detail for a different asset.')
    }
    return { kind: 'available', data: detail }
  } catch (error) {
    return unavailable(error)
  }
}

export const getV1AssetDetail = createServerFn()
  .inputValidator(
    z.object({ environment: environmentSchema, assetId: z.string().min(1) }),
  )
  .handler(({ data }) => getV1AssetDetailData(data))

export async function getV1AssetPreviewData({
  environment,
  assetId,
}: {
  environment: ObservatoryEnvironment
  assetId: string
}): Promise<V1TablesRead<V1AssetPreview>> {
  try {
    const preview = previewSchema.parse(
      await apiGet(`/api/v1/assets/${encodeURIComponent(assetId)}/preview`, {
        env: environment,
        limit: 100,
      }),
    )
    if (preview.env !== environment || preview.asset_id !== assetId) {
      throw new Error(
        'phlo-api returned a preview for a different asset or environment.',
      )
    }
    return { kind: 'available', data: preview }
  } catch (error) {
    return unavailable(error)
  }
}

export const getV1AssetPreview = createServerFn()
  .inputValidator(
    z.object({ environment: environmentSchema, assetId: z.string().min(1) }),
  )
  .handler(({ data }) => getV1AssetPreviewData(data))
