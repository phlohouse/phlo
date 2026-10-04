/** Read-only service health from the canonical v1 services resource. */
import { createFileRoute } from '@tanstack/react-router'
import { Server } from 'lucide-react'

import type { ObservatoryService } from '@/observatory/api/types'
import { getObservatoryServices } from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/services')({ component: Services })

function Services() {
  const result = useLiveResource(
    getObservatoryServices,
    30_000,
    'observatory:v1:services',
  )
  const services = result.data

  return (
    <ObservatoryPage
      kicker="Platform"
      title="Services"
      description="Live health, status, and response-time observations from the Phlo API."
      action={
        <span className="phlo-observatory-pill">
          {result.isLoading
            ? 'Loading'
            : result.error || services === null
              ? 'Unavailable'
              : `${services.length} services`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell">
        <div className="phlo-observatory-command-primary">
          {result.error && (
            <div className="phlo-observatory-panel-note" role="status">
              {result.error}
            </div>
          )}
          <div className="phlo-observatory-workspace-toolbar">
            <span>
              <Server className="size-4" />
              Service health
            </span>
            <span className="phlo-observatory-pill">Read only</span>
          </div>
          {services?.map((service) => (
            <ServiceRow key={service.id} service={service} />
          ))}
          {!result.isLoading && services?.length === 0 && !result.error && (
            <div className="phlo-observatory-operation-empty">
              <div>
                <h2>No services reported</h2>
                <p>The API returned an empty service inventory.</p>
              </div>
            </div>
          )}
          {result.isLoading && (
            <div className="phlo-observatory-operation-empty">
              <div>
                <h2>Loading service health</h2>
              </div>
            </div>
          )}
        </div>
        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">API coverage</div>
          <h2>Health and status only</h2>
          <p>
            The v1 services contract does not expose service detail, start,
            stop, restart, or package-install actions. Those controls are not
            offered here.
          </p>
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function ServiceRow({ service }: { service: ObservatoryService }) {
  const observedAt = service.metadata.observed_at
  const responseTime = service.metadata.response_time_seconds

  return (
    <div
      className="phlo-observatory-mini-row"
      data-state={service.health.state}
    >
      <span>
        <strong>{service.name}</strong>
        <small>{service.health.state}</small>
      </span>
      <span>{service.status}</span>
      <span>
        {typeof responseTime === 'number'
          ? `${responseTime} s`
          : 'Response time unavailable'}
      </span>
      <small>
        {typeof observedAt === 'string'
          ? `Observed ${observedAt}`
          : 'Observation time unavailable'}
      </small>
    </div>
  )
}
