/**
 * /observability route. Observability providers joined with service health;
 * the selection is persisted through ?providerId.
 */
import { createFileRoute } from '@tanstack/react-router'
import { Activity, Bell, Database, Radio } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryService,
  ObservatorySurfaceItem,
} from '@/observatory/api/types'
import {
  getObservatoryObservabilityItems,
  getObservatoryServices,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  metadataDisplayText,
  platformMetadataRows,
  rawMetadataText,
} from '@/observatory/platformMetadata'

export const Route = createFileRoute('/observability')({
  component: Observability,
})

export function Observability() {
  const result = useLiveResource(
    getObservatoryObservabilityItems,
    120_000,
    'observatory:observability',
  )
  const servicesResult = useLiveResource(
    getObservatoryServices,
    120_000,
    'observatory:services',
  )
  const items = result.data ?? []
  const services = servicesResult.data ?? []
  const isLoading = result.isLoading || servicesResult.isLoading
  const isInitialLoading = result.isLoading && items.length === 0
  const refreshState = isLoading
    ? isInitialLoading
      ? 'checking'
      : 'refreshing'
    : null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected =
    items.find((item) => item.id === selectedId) ?? items[0] ?? null
  const selectProvider = useCallback((providerId: string) => {
    setSelectedId(providerId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('providerId', providerId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])
  const summary = useMemo(
    () => summarizeObservability(items, services),
    [items, services],
  )
  const selectedDependencies = selected
    ? dependencyServices(selected, services)
    : []

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'providerId',
    )
    if (!requested || requested === selectedId) return
    if (items.some((item) => item.id === requested)) {
      setSelectedId(requested)
    }
  }, [items, selectedId])

  useEffect(() => {
    if (selectedId !== null || !selected) return
    setSelectedId(selected.id)
  }, [selected, selectedId])

  return (
    <ObservatoryPage
      kicker="Platform"
      title="Observability"
      description="Telemetry provider registration, expected backend, service dependencies, and alerting coverage."
      action={
        <span className="phlo-observatory-pill">
          {refreshState ? `${refreshState} · ` : ''}
          {items.length} providers
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell phlo-observatory-observability-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-platform-summary">
            <ObservabilityMetric
              icon={<Radio className="size-4" />}
              label="Registered"
              value={summary.registered}
            />
            <ObservabilityMetric
              icon={<Activity className="size-4" />}
              label="Backends"
              value={summary.backends}
            />
            <ObservabilityMetric
              icon={<Database className="size-4" />}
              label="Services running"
              value={`${summary.runningDeps}/${summary.requiredDeps}`}
            />
            <ObservabilityMetric
              icon={<Bell className="size-4" />}
              label="Alert sinks"
              value={summary.alertSinks}
            />
          </div>
          <div className="phlo-observatory-browser-toolbar">
            <span>
              <Radio className="size-4" />
              Providers
            </span>
            <span className="phlo-observatory-pill">
              {refreshState ? `${refreshState} · ` : ''}
              {items.length} registered
            </span>
          </div>
          <div className="phlo-observatory-platform-table" role="table">
            <div className="phlo-observatory-platform-head" role="row">
              <span>Provider</span>
              <span>Capability</span>
              <span>Backend</span>
              <span>Required services</span>
              <span>State</span>
            </div>
            {items.map((item) => (
              <ProviderRow
                item={item}
                key={item.id}
                onSelect={() => selectProvider(item.id)}
                selected={item.id === selected?.id}
                services={services}
              />
            ))}
            {isInitialLoading ? (
              <div className="phlo-observatory-run-provider-empty">
                <div>
                  <span className="phlo-observatory-inspector-label">
                    Observability
                  </span>
                  <h2>Loading observability providers</h2>
                  <p>Reading live telemetry and alerting provider records.</p>
                </div>
              </div>
            ) : (
              items.length === 0 && (
                <div className="phlo-observatory-run-provider-empty">
                  <div>
                    <span className="phlo-observatory-inspector-label">
                      Observability
                    </span>
                    <h2>No observability providers configured</h2>
                    <p>
                      The active stack has no telemetry or alerting provider
                      records to inspect.
                    </p>
                  </div>
                </div>
              )
            )}
          </div>
        </div>

        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">
            Provider detail
          </div>
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <p>{selected.summary ?? 'No provider summary available.'}</p>
              <dl className="phlo-observatory-facts">
                <Fact label="Health" value={selected.health.state} />
                <Fact
                  label="Backend"
                  value={metadataDisplayText(selected, 'backend')}
                />
                <Fact
                  label="Capability"
                  value={metadataDisplayText(selected, 'capability_type')}
                />
                <Fact
                  label="Provider"
                  value={metadataDisplayText(selected, 'provider')}
                />
              </dl>
              <div className="phlo-observatory-detail-list">
                {selectedDependencies.map((service) => (
                  <div
                    className="phlo-observatory-mini-row"
                    data-state={service.runtime_state ?? service.status}
                    key={service.id}
                  >
                    <span>{service.name}</span>
                    <small>
                      {[
                        service.runtime_state ?? service.status,
                        service.in_stack ? 'in stack' : 'not in stack',
                        service.health.message,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </small>
                  </div>
                ))}
                {selectedDependencies.length === 0 && (
                  <div className="phlo-observatory-mini-row">
                    <span>Service dependencies</span>
                    <small>No required services declared</small>
                  </div>
                )}
              </div>
              <div className="phlo-observatory-detail-list">
                {platformMetadataRows(selected.metadata).map((row) => (
                  <div className="phlo-observatory-mini-row" key={row.label}>
                    <span>{row.label}</span>
                    <small>{row.value}</small>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <h2>
                {isInitialLoading
                  ? 'Checking provider detail'
                  : 'No provider selected'}
              </h2>
              <p>
                {isLoading
                  ? 'Reading live telemetry and service dependency evidence.'
                  : 'Select a provider to inspect telemetry and service evidence.'}
              </p>
            </>
          )}
          {result.error && (
            <div className="phlo-observatory-panel-footer">{result.error}</div>
          )}
          {servicesResult.error && (
            <div className="phlo-observatory-panel-footer">
              {servicesResult.error}
            </div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function ProviderRow({
  item,
  onSelect,
  selected,
  services,
}: {
  item: ObservatorySurfaceItem
  onSelect: () => void
  selected: boolean
  services: Array<ObservatoryService>
}) {
  const dependencies = dependencyServices(item, services)
  const running = dependencies.filter(
    (service) => (service.runtime_state ?? service.status) === 'running',
  ).length
  return (
    <button
      className="phlo-observatory-platform-row"
      data-active={selected}
      onClick={onSelect}
      role="row"
      type="button"
    >
      <span>{item.name}</span>
      <span>{metadataDisplayText(item, 'capability_type')}</span>
      <span>{metadataDisplayText(item, 'backend')}</span>
      <span>
        {dependencies.length
          ? `${running}/${dependencies.length} running`
          : 'No dependency declared'}
      </span>
      <span>{item.health.message ?? item.health.state}</span>
    </button>
  )
}

function ObservabilityMetric({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: string | number
}) {
  return (
    <div className="phlo-observatory-platform-summary-cell">
      <span>
        {icon}
        {label}
      </span>
      <strong>{value}</strong>
    </div>
  )
}

function summarizeObservability(
  items: Array<ObservatorySurfaceItem>,
  services: Array<ObservatoryService>,
): {
  registered: number
  backends: number
  requiredDeps: number
  runningDeps: number
  alertSinks: number
} {
  const backends = new Set<string>()
  const required = new Set<string>()
  for (const item of items) {
    const backend = rawMetadataText(item, 'backend')
    if (backend !== 'not reported') backends.add(backend)
    for (const dependency of serviceDependencyIds(item)) {
      required.add(dependency)
    }
  }
  const runningDeps = [...required].filter((dependencyId) => {
    const service = services.find((candidate) => candidate.id === dependencyId)
    return (service?.runtime_state ?? service?.status) === 'running'
  }).length
  return {
    registered: items.length,
    backends: backends.size,
    requiredDeps: required.size,
    runningDeps,
    alertSinks: items.filter(
      (item) => rawMetadataText(item, 'capability_type') === 'alert_sink',
    ).length,
  }
}

function dependencyServices(
  item: ObservatorySurfaceItem,
  services: Array<ObservatoryService>,
): Array<ObservatoryService> {
  const dependencyIds = serviceDependencyIds(item)
  return dependencyIds.flatMap((dependencyId) => {
    const service = services.find((candidate) => candidate.id === dependencyId)
    return service ? [service] : []
  })
}

function serviceDependencyIds(item: ObservatorySurfaceItem): Array<string> {
  const value = item.metadata.service_dependencies
  if (!Array.isArray(value)) return []
  return value.filter((entry): entry is string => typeof entry === 'string')
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
