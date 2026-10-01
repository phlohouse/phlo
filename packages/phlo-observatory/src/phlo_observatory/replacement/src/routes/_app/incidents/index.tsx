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
  FilterSelect,
  IncidentGroup,
  applyFilters,
} from '@/components/incidents/list'
import { NewIncidentDialog } from '@/components/incidents/new-incident-dialog'

export const Route = createFileRoute('/_app/incidents/')({
  validateSearch: z.object({ dialog: z.enum(['new-incident']).optional() }),
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getIncidentList({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Incidents · phlo' }] }),
  component: IncidentsPage,
})

function IncidentsPage() {
  const { incidents, stats, truncated } = Route.useLoaderData()
  const { env, dialog } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const router = useRouter()
  const [filters, setFilters] = React.useState<Filters>({})
  const [creating, setCreating] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const shown = applyFilters(incidents, filters)
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
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto pb-6">
        <section
          aria-label="Incident stats"
          className="grid grid-cols-3 gap-px border-b border-line bg-line"
        >
          {(['open', 'acknowledged', 'resolved'] as const).map((status) => (
            <div key={status} className="bg-card px-4 py-4 lg:px-5">
              <Eyebrow>{status}</Eyebrow>
              <div className="mt-1 text-2xl font-medium">
                {stats[status] ?? 0}
              </div>
            </div>
          ))}
        </section>
        <div
          className="flex flex-wrap gap-3 border-b border-line px-4 py-3 lg:px-5"
          role="group"
          aria-label="Filters"
        >
          {(['status', 'kind', 'owner'] as Array<FilterKey>).map((key) => (
            <FilterSelect
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
            title={`${shown.length} incidents`}
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
