/**
 * Metadata grid: renders platform metadata through the shared label/value
 * vocabulary, omitting secrets and payloads.
 */
import { platformMetadataRows } from '@/observatory/platformMetadata'
import { Fact, FactGrid } from '@/components/observatory/key-value'

export function MetadataGrid({
  metadata,
}: {
  metadata: Record<string, unknown>
}) {
  const rows = platformMetadataRows(metadata)
  if (!rows.length) {
    return <p className="text-muted-foreground text-xs">No metadata.</p>
  }
  return (
    <FactGrid>
      {rows.map((row) => (
        <Fact key={row.label} label={row.label} value={row.value} />
      ))}
    </FactGrid>
  )
}
