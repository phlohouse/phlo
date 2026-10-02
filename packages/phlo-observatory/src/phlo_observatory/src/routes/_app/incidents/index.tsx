/** Defines the incident list route with filters and creation controls. */
import * as React from 'react'
import {
  Link,
  createFileRoute,
  useNavigate,
  useRouter,
} from '@tanstack/react-router'
import { PlusIcon } from 'lucide-react'
import { z } from 'zod'
import type { NewIncidentValues } from '@/components/incidents/new-incident-dialog'
import type { FilterKey, Filters } from '@/components/incidents/list'
import {
  createIncident,
  getIncidentList,
  incidentOperationKey,
} from '@/lib/data/api/incidents'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Button, buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  FilterChip,
  IncidentGroup,
  applyFilters,
} from '@/components/incidents/list'
import { Segmented } from '@/components/ui/toggle-group'
import { NewIncidentDialog } from '@/components/incidents/new-incident-dialog'

export const Route = createFileRoute('/_app/incidents/')({
  validateSearch: z.object({
    dialog: z.enum(['new-incident']).optional(),
    view: z.enum(['open', 'resolved']).optional(),
  }),
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getIncidentList({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Incidents · phlo' }] }),
  component: IncidentsPage,
})

function IncidentsPage() {
  const { incidents, stats, truncated } = Route.useLoaderData()
  const { env, dialog, view = 'open' } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const router = useRouter()
  const [filters, setFilters] = React.useState<Filters>({})
  const [creating, setCreating] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const open = incidents.filter((incident) => incident.status !== 'resolved')
  const resolved = incidents.filter(
    (incident) => incident.status === 'resolved',
  )
  const shown = applyFilters(
    view === 'resolved' ? resolved : open,
    filters,
  ).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  const options = (key: FilterKey) =>
    Array.from(
      new Set(incidents.map((item) => item[key] ?? 'Unassigned')),
    ).sort()
  const close = () =>
    navigate({
      search: (current) => ({ ...current, dialog: undefined }),
      replace: true,
    })

  async function create(values: NewIncidentValues) {
    setCreating(true)
    setError(undefined)
    try {
      const intent = JSON.stringify(values)
      const key = incidentOperationKey(env, 'new', 'create', intent)
      await createIncident({
        data: {
          env,
          idempotency_key: key,
          asset_id: values.assetId,
          kind: values.kind,
          title: values.title,
          evidence_id: `manual:${key}`,
          evidence: { description: values.description, source: 'observatory' },
        },
      })
      close()
      await router.invalidate()
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : 'Could not create incident.',
      )
    } finally {
      setCreating(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Incidents"
        actions={
          <>
            <Segmented
              aria-label="Show incidents"
              value={view}
              onValueChange={(value) =>
                navigate({
                  search: (current) => ({
                    ...current,
                    view: value === 'resolved' ? 'resolved' : undefined,
                  }),
                })
              }
              options={[
                { value: 'open', label: `Open · ${open.length}` },
                { value: 'resolved', label: `Resolved · ${resolved.length}` },
              ]}
            />
            <Link
              to="/incidents"
              search={{ env, dialog: 'new-incident' }}
              className={cn(
                buttonVariants(),
                'h-10 hover:text-primary-foreground lg:h-8',
              )}
            >
              <PlusIcon /> New incident
            </Link>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto pb-6">
        <section
          aria-label="Triage stats"
          className="grid grid-cols-2 gap-px border-b border-line bg-line lg:grid-cols-4"
        >
          <TriageCell
            label="Open"
            value={stats.open ?? 0}
            note={`${stats.acknowledged ?? 0} acknowledged`}
          />
          <TriageCell
            label="Time to acknowledge · 30 d"
            value="—"
            note="Unavailable"
          />
          <TriageCell
            label="Time to resolve · 30 d"
            value="—"
            note="Unavailable"
          />
          <TriageCell
            label="Resolved"
            value={stats.resolved ?? 0}
            note="all time"
          />
        </section>
        <div
          className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3 lg:px-5"
          role="group"
          aria-label="Filters"
        >
          <Button
            variant="outline"
            disabled
            title="Severity is not supplied by the incident API."
          >
            Severity
          </Button>
          <Button
            variant="outline"
            disabled
            title="Asset layers are not supplied by the incident API."
          >
            Layer
          </Button>
          {(['status', 'kind', 'owner'] as Array<FilterKey>).map((key) => (
            <FilterChip
              key={key}
              label={key[0].toUpperCase() + key.slice(1)}
              value={filters[key]}
              options={options(key)}
              onChange={(value) =>
                setFilters((current) => ({ ...current, [key]: value }))
              }
            />
          ))}
          {Object.values(filters).some(Boolean) ? (
            <Button variant="link" onClick={() => setFilters({})}>
              Clear all
            </Button>
          ) : null}
        </div>
        {shown.length ? (
          <IncidentGroup
            headed
            title={`${view === 'resolved' ? 'Resolved' : 'Open'} · ${shown.length}`}
            note={truncated ? 'First 500 results' : undefined}
            incidents={shown}
          />
        ) : (
          <EmptyState
            className="m-4 lg:m-5"
            title={
              incidents.length
                ? 'No incidents match these filters'
                : 'No incidents recorded'
            }
          >
            {incidents.length
              ? 'Clear filters to see all persisted incidents.'
              : 'No persisted incident evidence exists in this environment.'}
          </EmptyState>
        )}
        {view === 'open' && resolved.length ? (
          <IncidentGroup
            headed
            title={`Resolved history · ${resolved.length}`}
            note="Resolution timestamps are not supplied by the list API"
            incidents={applyFilters(resolved, filters)}
          />
        ) : null}
      </div>
      <NewIncidentDialog
        open={dialog === 'new-incident'}
        busy={creating}
        error={error}
        onClose={close}
        onCreate={create}
      />
    </>
  )
}

function TriageCell({
  label,
  value,
  note,
}: {
  label: string
  value: React.ReactNode
  note: string
}) {
  return (
    <div className="flex flex-col gap-1.5 bg-card px-4 py-3.5 lg:px-5 lg:py-4">
      <Eyebrow>{label}</Eyebrow>
      <div className="flex items-baseline gap-1.5">
        <span className="text-[22px] font-medium lg:text-2xl">{value}</span>
        <span className="text-[13px] text-muted-foreground">{note}</span>
      </div>
    </div>
  )
}
