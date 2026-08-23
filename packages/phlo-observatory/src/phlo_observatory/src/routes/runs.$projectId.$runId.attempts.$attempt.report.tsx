/**
 * Run attempt report route for
 * /runs/$projectId/$runId/attempts/$attempt/report. The loader fetches the
 * report; RunReportView also renders the pending state with a null result.
 */
import { createFileRoute } from '@tanstack/react-router'

import { getObservatoryRunReport } from '@/observatory/api/resources'
import { RunReportView } from '@/observatory/components/RunReportView'

export const Route = createFileRoute(
  '/runs/$projectId/$runId/attempts/$attempt/report',
)({
  loader: ({ params }) =>
    getObservatoryRunReport({
      data: {
        projectId: params.projectId,
        runId: params.runId,
        attempt: params.attempt,
      },
    }),
  pendingComponent: RunReportPending,
  component: RunReportRoute,
})

function RunReportRoute() {
  const params = Route.useParams()
  return <RunReportView request={params} result={Route.useLoaderData()} />
}

function RunReportPending() {
  const params = Route.useParams()
  return <RunReportView request={params} result={null} />
}
