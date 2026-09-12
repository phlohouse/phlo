/**
 * Index route — the lakehouse map. The loader fetches the overview
 * snapshot before render and passes it to the shared OverviewRoute
 * component as initial data. ?node= holds the selected map node.
 */
import { createFileRoute } from '@tanstack/react-router'

import {
  OverviewRoute,
  loadOverviewSnapshotFromApi,
} from '@/observatory/routes/OverviewRoute'

export const Route = createFileRoute('/')({
  loader: loadOverviewSnapshotFromApi,
  validateSearch: (search: Record<string, unknown>) =>
    typeof search.node === 'string' ? { node: search.node } : {},
  component: ObservatoryIndexOverviewRoute,
})

function ObservatoryIndexOverviewRoute() {
  const snapshot = Route.useLoaderData()
  return <OverviewRoute initialSnapshot={snapshot} />
}
