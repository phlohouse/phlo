/**
 * /recents route. Lists recently opened resources from browser-local
 * activity history and refreshes on the localActivity event.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Clock3, ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { ObservatoryRecentVisit } from '@/observatory/shell/localActivity'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  localActivityEvent,
  readRecentVisits,
} from '@/observatory/shell/localActivity'

export const Route = createFileRoute('/recents')({ component: Recents })

export function Recents() {
  const [visits, setVisits] = useState<Array<ObservatoryRecentVisit>>([])

  useEffect(() => {
    const refresh = () => setVisits(readRecentVisits())
    refresh()
    window.addEventListener(localActivityEvent, refresh)
    return () => window.removeEventListener(localActivityEvent, refresh)
  }, [])

  return (
    <ObservatoryPage
      kicker="Workspace"
      title="Recents"
      description="Resources opened in this browser, kept locally to preserve continuity without claiming shared runtime history."
      action={
        <span className="phlo-observatory-pill">{visits.length} recent</span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-local-index-shell">
        <div className="phlo-observatory-command-primary">
          <div className="phlo-observatory-workspace-toolbar">
            <span>
              <Clock3 className="size-4" />
              Recently opened
            </span>
            <span className="phlo-observatory-pill">Browser-local</span>
          </div>
          {visits.length ? (
            <div className="phlo-observatory-detail-list">
              {visits.map((visit) => (
                <Link
                  className="phlo-observatory-local-index-row"
                  key={visit.path}
                  to={visit.path}
                >
                  <span>
                    <strong>{visit.label}</strong>
                    <small>{visit.path}</small>
                  </span>
                  <span>
                    <small>{formatTime(visit.visitedAt)}</small>
                    <ExternalLink className="size-3.5" />
                  </span>
                </Link>
              ))}
            </div>
          ) : (
            <div className="phlo-observatory-operation-empty">
              <div>
                <h2>No recent resources yet</h2>
                <p>
                  Open a Dataset, run, query, or platform surface to build this
                  local list.
                </p>
              </div>
            </div>
          )}
        </div>
        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">Evidence scope</div>
          <h2>Local navigation history</h2>
          <p>
            This list belongs to the current browser. It is not presented as
            shared project or runtime evidence.
          </p>
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
