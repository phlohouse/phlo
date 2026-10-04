/** Sorting model and page-value accessors for the asset inventory table. */
import {
  createColumnHelper,
  createSortedRowModel,
  rowSortingFeature,
  sortFn_text,
  tableFeatures,
} from '@tanstack/react-table'
import { assetFreshness, assetLayer } from './api/assets'
import type { ApiAsset } from './api/assets'

export const assetTableFeatures = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { text: sortFn_text },
})

const column = createColumnHelper<typeof assetTableFeatures, ApiAsset>()

export const assetTableColumns = column.columns([
  column.accessor('id', {
    id: 'asset',
    header: 'Asset',
    sortFn: 'text',
  }),
  column.accessor((asset) => assetLayer(asset), {
    id: 'layer',
    header: 'Layer',
    sortFn: 'text',
    sortUndefined: 'last',
  }),
  column.accessor((asset) => assetFreshness(asset), {
    id: 'freshness',
    header: 'Freshness',
    sortFn: 'text',
  }),
  column.accessor(
    (asset) => assetTimestampSortValue(asset.last_materialization_at),
    {
      id: 'lastMaterialized',
      header: 'Last materialized',
      sortUndefined: 'last',
    },
  ),
  column.accessor((asset) => assetNumericSortValue(asset.row_count), {
    id: 'rowCount',
    header: 'Rows',
    sortUndefined: 'last',
  }),
  column.accessor((asset) => assetNumericSortValue(asset.size_bytes), {
    id: 'sizeBytes',
    header: 'Size',
    sortUndefined: 'last',
  }),
  column.accessor((asset) => asset.owner ?? undefined, {
    id: 'owner',
    header: 'Owner',
    sortFn: 'text',
    sortUndefined: 'last',
  }),
  column.display({
    id: 'writes',
    header: '7d observed writes',
    enableSorting: false,
  }),
])

export const assetSortOptions = [
  { id: 'asset', label: 'Asset' },
  { id: 'layer', label: 'Layer' },
  { id: 'freshness', label: 'Freshness' },
  { id: 'lastMaterialized', label: 'Last materialized' },
  { id: 'rowCount', label: 'Rows' },
  { id: 'sizeBytes', label: 'Size' },
  { id: 'owner', label: 'Owner' },
] as const

export function assetNumericSortValue(
  value: number | null | undefined,
): number | undefined {
  return value ?? undefined
}

export function assetTimestampSortValue(
  value: string | null | undefined,
): number | undefined {
  if (!value) return undefined
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : undefined
}

export function formatAssetTimestamp(value: string | null | undefined): string {
  if (!value) return 'Not observed'
  const timestamp = new Date(value)
  if (!Number.isFinite(timestamp.getTime())) return 'Invalid timestamp'
  return `${timestamp.toLocaleString('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  })} UTC`
}
