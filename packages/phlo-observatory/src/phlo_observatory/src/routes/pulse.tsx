/**
 * /pulse — the live event stream. ?k= filters by kind, ?level= by level.
 */
import { createFileRoute, useNavigate } from '@tanstack/react-router'

import type { PulseKind } from '@/components/pulse/pulse-model'
import type { PulseLevelFilter } from '@/observatory/routes/PulseRoute'
import { PulseRoute } from '@/observatory/routes/PulseRoute'

const KINDS = new Set(['operation', 'log'])
const LEVELS = new Set(['error', 'warning'])

export const Route = createFileRoute('/pulse')({
  validateSearch: (search: Record<string, unknown>) => ({
    ...(typeof search.k === 'string' && KINDS.has(search.k)
      ? { k: search.k as PulseKind }
      : {}),
    ...(typeof search.level === 'string' && LEVELS.has(search.level)
      ? { level: search.level as PulseLevelFilter }
      : {}),
  }),
  component: PulsePage,
})

function PulsePage() {
  const { k, level } = Route.useSearch()
  const navigate = useNavigate({ from: '/pulse' })
  return (
    <PulseRoute
      kind={k ?? 'all'}
      level={level ?? 'all'}
      onKind={(kind) =>
        void navigate({
          replace: true,
          search: (prev) => ({
            ...prev,
            k: kind === 'all' ? undefined : kind,
          }),
        })
      }
      onLevel={(next) =>
        void navigate({
          replace: true,
          search: (prev) => ({
            ...prev,
            level: next === 'all' ? undefined : next,
          }),
        })
      }
    />
  )
}
