/**
 * Human-readable labels for platform metadata keys and values rendered in
 * Observatory detail views.
 */
type MetadataOwner = {
  metadata: Record<string, unknown>
}

const metadataLabels: Record<string, string> = {
  backend_kind: 'Backend',
  capability_type: 'Capability',
  catalog_present: 'Dataset availability',
  catalog_state: 'Dataset availability',
  default_database: 'Default database',
  provider: 'Provider',
  service_dependencies: 'Service dependencies',
  service_name: 'Service',
  service_type: 'Service type',
  storage_system: 'Storage system',
  target_system: 'Target system',
}

const valueLabels: Record<string, string> = {
  alert_sink: 'Alert sink',
  fastapi: 'FastAPI',
  iceberg: 'Iceberg',
  model_only: 'Model only',
  object_store: 'Object store',
  phlo_api: 'phlo-api',
  publish_target: 'Publish target',
  query_engine: 'Query engine',
  queryable: 'Queryable',
  table_store: 'Table store',
  trino: 'Trino',
}

const omittedMetadataKeys = new Set(['description'])
// Credentials, hashes, and raw config/payload blobs never reach the metadata
// panels: they are either sensitive or pure noise in the UI.
const omittedMetadataKeyPatterns = [
  /(^|_)hash$/,
  /(^|_)checksum$/,
  /(^|_)token$/,
  /(^|_)secret$/,
  /(^|_)password$/,
  /^config$/,
  /_config$/,
  /^raw$/,
  /_raw$/,
  /^payload$/,
  /_payload$/,
]

export type PlatformMetadataRow = {
  label: string
  value: string
}

export function rawMetadataText(item: MetadataOwner, key: string): string {
  const value = item.metadata[key]
  if (typeof value === 'string' && value.trim()) return value
  return 'not reported'
}

export function metadataDisplayText(item: MetadataOwner, key: string): string {
  return formatPlatformMetadata(item.metadata[key])
}

export function platformMetadataRows(
  metadata: Record<string, unknown>,
): Array<PlatformMetadataRow> {
  return Object.entries(metadata)
    .filter(([key]) => !shouldOmitMetadataKey(key))
    .map(([key, value]) => ({
      label: metadataLabel(key),
      value: formatPlatformMetadata(value),
    }))
}

export function metadataLabel(key: string): string {
  return metadataLabels[key] ?? titleize(key)
}

export function formatPlatformMetadata(value: unknown): string {
  if (value === null || value === undefined) return 'unset'
  if (Array.isArray(value)) {
    return value.map(formatPlatformMetadata).join(', ') || 'none'
  }
  if (typeof value === 'string') return labelValue(value)
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  if (
    typeof value === 'object' &&
    value !== null &&
    'target' in value &&
    typeof value.target === 'string'
  ) {
    const checks =
      'checks' in value && Array.isArray(value.checks)
        ? ` · ${value.checks.length} checks`
        : ''
    return `${labelValue(value.target)}${checks}`
  }
  if (typeof value === 'object' && value !== null) {
    const keys = Object.keys(value)
    return keys.length
      ? `structured metadata · ${keys.length} fields`
      : 'structured metadata'
  }
  return String(value)
}

export function labelValue(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) return 'not reported'
  return (
    valueLabels[trimmed] ??
    (trimmed.includes('_') ? titleize(trimmed) : trimmed)
  )
}

function titleize(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function shouldOmitMetadataKey(key: string): boolean {
  return (
    omittedMetadataKeys.has(key) ||
    omittedMetadataKeyPatterns.some((pattern) => pattern.test(key))
  )
}
