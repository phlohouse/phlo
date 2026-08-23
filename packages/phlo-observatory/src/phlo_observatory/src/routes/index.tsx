/**
 * Index route. The loader fetches the overview snapshot before render and
 * passes it to the shared OverviewRoute component as initial data.
 */
import { createFileRoute } from '@tanstack/react-router'

import {
  OverviewRoute,
  loadOverviewSnapshotFromApi,
} from '@/observatory/routes/OverviewRoute'

export const Route = createFileRoute('/')({
  loader: loadOverviewSnapshotFromApi,
  component: ObservatoryIndexOverviewRoute,
})

function ObservatoryIndexOverviewRoute() {
  const snapshot = Route.useLoaderData()
  return <OverviewRoute initialSnapshot={snapshot} />
}
