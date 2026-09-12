/**
 * /now — the ranked triage queue. ?kind= filters by item kind.
 */
import { createFileRoute, useNavigate } from '@tanstack/react-router'

import type { TriageKind } from '@/components/now/triage-model'
import { NowRoute } from '@/observatory/routes/NowRoute'

const KINDS = new Set(['run', 'check', 'dataset', 'service'])

export const Route = createFileRoute('/now')({
  validateSearch: (search: Record<string, unknown>) =>
    typeof search.kind === 'string' && KINDS.has(search.kind)
      ? { kind: search.kind as TriageKind }
      : {},
  component: NowPage,
})

function NowPage() {
  const { kind } = Route.useSearch()
  const navigate = useNavigate({ from: '/now' })
  return (
    <NowRoute
      filter={kind ?? 'all'}
      onFilter={(next) =>
        void navigate({
          replace: true,
          search: next === 'all' ? {} : { kind: next },
        })
      }
    />
  )
}
