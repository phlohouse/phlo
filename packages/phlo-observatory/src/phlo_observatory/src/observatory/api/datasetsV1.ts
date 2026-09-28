/**
 * Dataset boundary for replacement screens.
 *
 * v1 exposes Dagster assets, but does not expose the governed Dataset
 * inventory, profile, readiness, publication, or candidate-transition
 * contracts these routes require. Assets must not be projected as Datasets.
 */
export type DatasetV1Surface = 'list' | 'detail'

export type DatasetV1Read = {
  kind: 'unavailable'
  message: string
}

export function datasetV1Unavailable(surface: DatasetV1Surface): DatasetV1Read {
  const label = surface === 'list' ? 'inventory' : 'detail'
  return {
    kind: 'unavailable',
    message: `The governed Dataset ${label} is unavailable because /api/v1 has no Dataset contract for it.`,
  }
}
