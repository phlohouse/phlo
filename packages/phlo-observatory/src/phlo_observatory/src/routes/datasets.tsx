/** Core Dataset inventory requires a Dataset-specific v1 contract. */
import {
  Link,
  Outlet,
  createFileRoute,
  useMatches,
} from '@tanstack/react-router'

import { datasetV1Unavailable } from '@/observatory/api/datasetsV1'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

export const Route = createFileRoute('/datasets')({ component: Datasets })

export function Datasets() {
  const matches = useMatches()
  const showingDetail = matches.some(
    (match) => match.routeId === '/datasets/$datasetId',
  )

  if (showingDetail) return <Outlet />

  return (
    <ObservatoryPage
      kicker="Lakehouse"
      title="Datasets"
      description="Governed Dataset inventory."
    >
      <section className="phlo-observatory-operation-empty">
        <div>
          <span className="phlo-observatory-inspector-label">
            Dataset inventory unavailable
          </span>
          <h2>No matching v1 Dataset contract</h2>
          <p>{datasetV1Unavailable('list').message}</p>
          <p>
            Dagster assets are operational entities, not governed Datasets, and
            are not shown here as substitutes.
          </p>
          <Link className="phlo-observatory-map-action" to="/lineage">
            Browse operational lineage
          </Link>
        </div>
      </section>
    </ObservatoryPage>
  )
}
