/**
 * /recents route. Lists recently opened resources from browser-local
 * activity history and refreshes on the localActivity event.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { ChevronRight, Clock3 } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { ObservatoryRecentVisit } from '@/observatory/shell/localActivity'
import {
  localActivityEvent,
  readRecentVisits,
} from '@/observatory/shell/localActivity'
import { Page, PageHeader } from '@/components/observatory/page'
import { EmptyBlock } from '@/components/observatory/states'
import { formatRelativeTime } from '@/components/observatory/time'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'

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
    <Page>
      <PageHeader
        actions={<Badge variant="secondary">{visits.length} recent</Badge>}
        description="Resources opened in this browser — kept locally to preserve continuity, not shared runtime history."
        title="Recents"
      />
      <SectionCard
        actions={<Badge variant="secondary">browser-local</Badge>}
        title="Recently opened"
      >
        {visits.length ? (
          <div className="divide-y divide-border">
            {visits.map((visit) => (
              <Link
                className="hover:bg-accent/50 flex items-center gap-3 px-3 py-2 transition-colors"
                key={visit.path}
                to={visit.path}
              >
                <Clock3 className="text-muted-foreground size-3.5 flex-none" />
                <span className="min-w-0 flex-1">
                  <span className="text-foreground block truncate text-xs font-medium">
                    {visit.label}
                  </span>
                  <span className="text-muted-foreground block truncate font-mono text-[11px]">
                    {visit.path}
                  </span>
                </span>
                <span className="text-muted-foreground font-mono text-[10px]">
                  {formatRelativeTime(visit.visitedAt)}
                </span>
                <ChevronRight className="text-muted-foreground size-3.5" />
              </Link>
            ))}
          </div>
        ) : (
          <EmptyBlock
            description="Open a dataset, run, query, or platform surface to build this local list."
            title="No recent resources yet"
          />
        )}
      </SectionCard>
    </Page>
  )
}
