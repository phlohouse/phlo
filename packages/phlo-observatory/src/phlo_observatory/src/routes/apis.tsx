/**
 * /apis route. Browses API contracts alongside running services, with the
 * selection mirrored into the ?apiId query parameter.
 */
import { createFileRoute } from '@tanstack/react-router'
import { Braces, Radio, Route as RouteIcon, Server } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryService,
  ObservatorySurfaceItem,
} from '@/observatory/api/types'
import {
  getObservatoryApiItems,
  getObservatoryServices,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  metadataDisplayText,
  platformMetadataRows,
  rawMetadataText,
} from '@/observatory/platformMetadata'

export const Route = createFileRoute('/apis')({
  component: APIs,
})

export function APIs() {
  const result = useLiveResource(
    getObservatoryApiItems,
    120_000,
    'observatory:apis',
  )
  const servicesResult = useLiveResource(
    getObservatoryServices,
    120_000,
    'observatory:services',
  )
  const contracts = result.data ?? []
  const services = servicesResult.data ?? []
  const isLoading = result.isLoading || servicesResult.isLoading
  const isInitialLoading = result.isLoading && contracts.length === 0
  const refreshState = isLoading
    ? isInitialLoading
      ? 'checking'
      : 'refreshing'
    : null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected =
    contracts.find((contract) => contract.id === selectedId) ??
    contracts[0] ??
    null
  const selectApi = useCallback((apiId: string) => {
    setSelectedId(apiId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('apiId', apiId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])
  const summary = useMemo(
    () => summarizeApis(contracts, services),
    [contracts, services],
  )
  const selectedService = selected
    ? serviceForContract(selected, services)
    : null

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get('apiId')
    if (!requested || requested === selectedId) return
    if (contracts.some((contract) => contract.id === requested)) {
      setSelectedId(requested)
    }
  }, [contracts, selectedId])

  useEffect(() => {
    if (selectedId !== null || !selected) return
    setSelectedId(selected.id)
  }, [selected, selectedId])

  return (
    <ObservatoryPage
      kicker="APIs"
      title="API surfaces"
      description="Published API contracts, backend providers, service attachment, and runtime readiness."
      action={
        <span className="phlo-observatory-pill">
          {refreshState ? `${refreshState} · ` : ''}
          {contracts.length} contracts
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell phlo-observatory-apis-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-platform-summary">
            <PlatformMetric
              icon={<Braces className="size-4" />}
              label="Contracts"
              value={summary.contracts}
            />
            <PlatformMetric
              icon={<RouteIcon className="size-4" />}
              label="Backends"
              value={summary.backends}
            />
            <PlatformMetric
              icon={<Server className="size-4" />}
              label="Services attached"
              value={`${summary.attachedServices}/${summary.contracts}`}
            />
            <PlatformMetric
              icon={<Radio className="size-4" />}
              label="Running"
              value={`${summary.runningServices}/${summary.attachedServices}`}
            />
          </div>
          <div className="phlo-observatory-browser-toolbar">
            <span>
              <Braces className="size-4" />
              API contracts
            </span>
            <span className="phlo-observatory-pill">
              {refreshState ? `${refreshState} · ` : ''}
              {contracts.length} registered
            </span>
          </div>
          <div className="phlo-observatory-platform-table" role="table">
            <div className="phlo-observatory-platform-head" role="row">
              <span>Contract</span>
              <span>Backend</span>
              <span>Service</span>
              <span>Runtime</span>
              <span>State</span>
            </div>
            {contracts.map((contract) => (
              <ApiRow
                contract={contract}
                key={contract.id}
                onSelect={() => selectApi(contract.id)}
                selected={contract.id === selected?.id}
                service={serviceForContract(contract, services)}
              />
            ))}
            {isInitialLoading ? (
              <div className="phlo-observatory-run-provider-empty">
                <div>
                  <span className="phlo-observatory-inspector-label">
                    API contracts
                  </span>
                  <h2>Loading API contracts</h2>
                  <p>
                    Reading live contracts, backend services, and runtime state.
                  </p>
                </div>
              </div>
            ) : (
              contracts.length === 0 && (
                <div className="phlo-observatory-run-provider-empty">
                  <div>
                    <span className="phlo-observatory-inspector-label">
                      API contracts
                    </span>
                    <h2>No API contracts configured</h2>
                    <p>
                      The active stack has no API provider records to inspect.
                    </p>
                  </div>
                </div>
              )
            )}
          </div>
        </div>

        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">API detail</div>
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <p>{selected.summary ?? 'No API summary available.'}</p>
              <dl className="phlo-observatory-facts">
                <Fact label="Health" value={selected.health.state} />
                <Fact
                  label="Provider"
                  value={metadataDisplayText(selected, 'provider')}
                />
                <Fact
                  label="Backend"
                  value={metadataDisplayText(selected, 'backend_kind')}
                />
                <Fact
                  label="Service"
                  value={metadataDisplayText(selected, 'service_name')}
                />
              </dl>
              <div className="phlo-observatory-detail-list">
                <div
                  className="phlo-observatory-mini-row"
                  data-state={
                    selectedService?.runtime_state ?? selectedService?.status
                  }
                >
                  <span>{metadataDisplayText(selected, 'service_name')}</span>
                  <small>
                    {selectedService
                      ? [
                          selectedService.runtime_state ??
                            selectedService.status,
                          selectedService.in_stack
                            ? 'in stack'
                            : 'not in stack',
                          selectedService.health.message,
                        ]
                          .filter(Boolean)
                          .join(' · ')
                      : 'No matching runtime service reported'}
                  </small>
                </div>
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
                {isInitialLoading ? 'Checking API detail' : 'No API selected'}
              </h2>
              <p>
                {isLoading
                  ? 'Reading live runtime and service context.'
                  : 'Select an API contract to inspect runtime and service context.'}
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

function ApiRow({
  contract,
  onSelect,
  selected,
  service,
}: {
  contract: ObservatorySurfaceItem
  onSelect: () => void
  selected: boolean
  service: ObservatoryService | null
}) {
  return (
    <button
      className="phlo-observatory-platform-row"
      data-active={selected}
      onClick={onSelect}
      role="row"
      type="button"
    >
      <span>{contract.name}</span>
      <span>{metadataDisplayText(contract, 'backend_kind')}</span>
      <span>{metadataDisplayText(contract, 'service_name')}</span>
      <span>{serviceRuntime(service)}</span>
      <span>{contract.health.message ?? contract.health.state}</span>
    </button>
  )
}

function PlatformMetric({
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

function summarizeApis(
  contracts: Array<ObservatorySurfaceItem>,
  services: Array<ObservatoryService>,
): {
  contracts: number
  backends: number
  attachedServices: number
  runningServices: number
} {
  const backends = new Set<string>()
  const attached = new Set<string>()
  for (const contract of contracts) {
    const backend = rawMetadataText(contract, 'backend_kind')
    if (backend !== 'not reported') backends.add(backend)
    const serviceName = rawMetadataText(contract, 'service_name')
    if (serviceName !== 'not reported') attached.add(serviceName)
  }
  const runningServices = [...attached].filter((serviceId) => {
    const service = services.find((candidate) => candidate.id === serviceId)
    return (service?.runtime_state ?? service?.status) === 'running'
  }).length
  return {
    contracts: contracts.length,
    backends: backends.size,
    attachedServices: attached.size,
    runningServices,
  }
}

function serviceForContract(
  contract: ObservatorySurfaceItem,
  services: Array<ObservatoryService>,
): ObservatoryService | null {
  const serviceName = rawMetadataText(contract, 'service_name')
  if (serviceName === 'not reported') return null
  return services.find((service) => service.id === serviceName) ?? null
}

function serviceRuntime(service: ObservatoryService | null): string {
  if (!service) return 'not reported'
  return service.runtime_state ?? service.status
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
