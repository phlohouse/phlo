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
    header: 'Last 7 days',
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

/** Compact inventory values retain exact counts in the cell's title. */
export function formatInventoryCount(value: number): string {
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

export function formatInventoryBytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent =
    value > 0 ? Math.min(4, Math.floor(Math.log10(value) / 3)) : 0
  return `${new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 }).format(value / 1000 ** exponent)} ${units[exponent]}`
}

export function formatInventoryAge(seconds: number): string {
  const minutes = Math.max(0, Math.floor(seconds / 60))
  if (minutes < 60) return `${minutes} m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h${minutes % 60 ? ` ${minutes % 60} m` : ''}`
  return `${Math.floor(hours / 24)} d${hours % 24 ? ` ${hours % 24} h` : ''}`
}

/** Empty days have no observation; they do not imply an SLA result or failure. */
export function assetMaterializationDays(
  asset: ApiAsset,
  observedAt = Date.now(),
) {
  const day = 86_400_000
  const start = Math.floor(observedAt / day) * day - 6 * day
  return Array.from({ length: 7 }, (_, index) => {
    const timestamp = start + index * day
    const count = (asset.materializations ?? []).filter((item) => {
      const at = Date.parse(item.timestamp)
      return at >= timestamp && at < timestamp + day && at <= observedAt
    }).length
    return { date: new Date(timestamp).toISOString().slice(0, 10), count }
  })
}

export function assetFreshnessLabel(
  asset: ApiAsset,
  observedAt = Date.now(),
): string {
  const status = assetFreshness(asset)
  const timestamp = Date.parse(asset.freshness_observed_at ?? '')
  const sla = asset.freshness_sla_seconds
  if (
    status === 'unknown' ||
    !Number.isFinite(timestamp) ||
    timestamp > observedAt ||
    sla == null ||
    sla <= 0
  )
    return status
  return `${formatInventoryAge((observedAt - timestamp) / 1000)} / ${formatInventoryAge(sla)}`
}
