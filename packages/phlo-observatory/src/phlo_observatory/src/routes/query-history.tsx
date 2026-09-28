import { Link, createFileRoute } from '@tanstack/react-router'
import { Clock3 } from 'lucide-react'

import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

export const Route = createFileRoute('/query-history')({
  component: QueryHistory,
})

function QueryHistory() {
  return (
    <ObservatoryPage
      kicker="Data"
      title="Query history"
      description="Shared query history is not available from the current Phlo API."
    >
      <div className="phlo-observatory-operation-empty">
        <div>
          <Clock3 className="size-5" />
          <h2>No server-backed history contract</h2>
          <p>
            Previous browser-local records are not shown as project query
            evidence. Query executions remain audited by the API.
          </p>
          <Link className="phlo-observatory-map-action" to="/queries">
            Open Queries
          </Link>
        </div>
      </div>
    </ObservatoryPage>
  )
}
