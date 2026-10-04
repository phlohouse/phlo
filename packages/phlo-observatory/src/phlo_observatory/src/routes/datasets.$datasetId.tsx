/** Dataset detail remains addressable while the Dataset v1 contract is absent. */
import { Link, createFileRoute } from '@tanstack/react-router'

import { datasetV1Unavailable } from '@/observatory/api/datasetsV1'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

export const Route = createFileRoute('/datasets/$datasetId')({
  component: DatasetProfileRoute,
})

function DatasetProfileRoute() {
  const { datasetId } = Route.useParams()
  return <DatasetProfile datasetId={datasetId} />
}

export function DatasetProfile({ datasetId }: { datasetId: string }) {
  return (
    <ObservatoryPage
      kicker="Dataset"
      title={datasetId}
      description="Requested governed Dataset context."
    >
      <section className="phlo-observatory-operation-empty">
        <div>
          <span className="phlo-observatory-inspector-label">
            Dataset detail unavailable
          </span>
          <h2>No matching v1 Dataset contract</h2>
          <p>{datasetV1Unavailable('detail').message}</p>
          <p>
            This route preserves the requested Dataset identifier but does not
            use Dagster asset data as Dataset evidence.
          </p>
          <Link className="phlo-observatory-map-action" to="/datasets">
            Return to Dataset inventory
          </Link>
        </div>
      </section>
    </ObservatoryPage>
  )
}
