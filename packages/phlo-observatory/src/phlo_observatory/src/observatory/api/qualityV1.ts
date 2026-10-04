/** Environment-scoped asset-check evidence from the v1 API. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'

import { apiGet } from '@/server/phlo-api'

const environmentSchema = z.object({ environment: z.enum(['prod', 'staging']) })
const assetSchema = z.object({ id: z.string().min(1) })
const assetsSchema = z.object({
  env: z.enum(['prod', 'staging']),
  items: z.array(assetSchema),
  next_cursor: z.string().nullable(),
})
const executionSchema = z.object({
  status: z.string().min(1),
  run_id: z.string().min(1),
  timestamp: z.string(),
  check_name: z.string().min(1),
  passed: z.boolean().nullable(),
  severity: z.string().nullable(),
  metadata: z.array(z.object({ label: z.string(), value: z.json() })),
})
const assetChecksSchema = z.object({
  env: z.enum(['prod', 'staging']),
  asset_id: z.string().min(1),
  definitions: z.array(
    z.object({ name: z.string().min(1), description: z.string().nullable() }),
  ),
  executions: z.array(executionSchema),
})

export type V1CheckExecution = z.infer<typeof executionSchema>
export type V1AssetChecks = z.infer<typeof assetChecksSchema>
export type V1QualityAsset =
  | { kind: 'available'; assetId: string; checks: V1AssetChecks }
  | { kind: 'unavailable'; assetId: string; message: string }
export type V1QualitySnapshot = {
  environment: z.infer<typeof environmentSchema>['environment']
  assets: Array<V1QualityAsset>
  truncated: boolean
}
export type V1QualityRead =
  | { kind: 'available'; data: V1QualitySnapshot }
  | { kind: 'unavailable'; message: string }

function unavailable(error: unknown): string {
  return error instanceof Error
    ? error.message
    : 'Quality evidence is unavailable.'
}

function assetChecksPath(assetId: string): string {
  return `/api/v1/assets/${assetId
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')}/checks`
}

async function loadAssetChecks(
  assetId: string,
  environment: z.infer<typeof environmentSchema>['environment'],
): Promise<V1QualityAsset> {
  try {
    const checks = assetChecksSchema.parse(
      await apiGet<unknown>(assetChecksPath(assetId), { env: environment }),
    )
    if (checks.env !== environment || checks.asset_id !== assetId) {
      throw new Error(
        'phlo-api returned quality evidence for another asset or environment.',
      )
    }
    return { kind: 'available', assetId, checks }
  } catch (error) {
    return { kind: 'unavailable', assetId, message: unavailable(error) }
  }
}

export async function getV1QualitySnapshotData(
  environment: z.infer<typeof environmentSchema>['environment'],
): Promise<V1QualityRead> {
  try {
    const assets = assetsSchema.parse(
      await apiGet<unknown>('/api/v1/assets', { env: environment, limit: 100 }),
    )
    if (assets.env !== environment) {
      throw new Error('phlo-api returned assets for another environment.')
    }
    return {
      kind: 'available',
      data: {
        environment,
        assets: await Promise.all(
          assets.items.map((asset) => loadAssetChecks(asset.id, environment)),
        ),
        truncated: assets.next_cursor !== null,
      },
    }
  } catch (error) {
    return { kind: 'unavailable', message: unavailable(error) }
  }
}

export const getV1QualitySnapshot = createServerFn()
  .inputValidator(environmentSchema)
  .handler(
    async ({ data }): Promise<V1QualityRead> =>
      getV1QualitySnapshotData(data.environment),
  )
