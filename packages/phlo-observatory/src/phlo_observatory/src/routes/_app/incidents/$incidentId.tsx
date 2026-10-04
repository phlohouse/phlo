/** Defines the incident detail route and loads its timeline and follow-ups. */
import { createFileRoute } from '@tanstack/react-router'
import { getIncidentDetail } from '@/lib/data/api/incidents'
import { PageHeader } from '@/components/phlo/page'
import { IncidentDetail } from '@/components/incidents/resolved'
import { IncidentActions } from '@/components/incidents/header-actions'

export const Route = createFileRoute('/_app/incidents/$incidentId')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ params, deps }) =>
    getIncidentDetail({ data: { env: deps.env, id: params.incidentId } }),
  head: ({ loaderData }) => ({
    meta: [
      {
        title: loaderData
          ? `#${loaderData.incident.id} ${loaderData.incident.title} · phlo`
          : 'Incident · phlo',
      },
    ],
  }),
  component: IncidentPage,
})

function IncidentPage() {
  const data = Route.useLoaderData()
  const { env } = Route.useSearch()
  return (
    <>
      <PageHeader
        crumbs={[{ label: 'Incidents', to: '/incidents' }]}
        title={`#${data.incident.id} ${data.incident.title}`}
        actions={<IncidentActions env={env} incident={data.incident} />}
      />
      <IncidentDetail env={env} {...data} />
    </>
  )
}
