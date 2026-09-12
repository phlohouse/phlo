/**
 * /apis route. Browses API contracts alongside running services, with the
 * selection mirrored into the ?apiId query parameter.
 */
import { createFileRoute } from '@tanstack/react-router'
import { Braces, Radio, Route as RouteIcon, Server } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ObservatoryService,
  ObservatorySurfaceItem,
} from '@/observatory/api/types'
import {
  getObservatoryApiItems,
  getObservatoryServices,
} from '@/observatory/api/resources'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  metadataDisplayText,
  platformMetadataRows,
  rawMetadataText,
} from '@/observatory/platformMetadata'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { HealthDot, StatusBadge } from '@/components/observatory/status'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

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
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {refreshState ? `${refreshState} · ` : ''}
            {contracts.length} contracts
          </Badge>
        }
        description="Published API contracts, backend providers, service attachment, and runtime readiness."
        title="API surfaces"
      />
      <StatGrid>
        <StatCard
          icon={<Braces className="size-3.5" />}
          label="Contracts"
          value={summary.contracts}
        />
        <StatCard
          icon={<RouteIcon className="size-3.5" />}
          label="Backends"
          value={summary.backends}
        />
        <StatCard
          icon={<Server className="size-3.5" />}
          label="Services attached"
          value={`${summary.attachedServices}/${summary.contracts}`}
        />
        <StatCard
          icon={<Radio className="size-3.5" />}
          label="Running"
          state={
            summary.attachedServices > 0 &&
            summary.runningServices < summary.attachedServices
              ? 'warning'
              : 'ok'
          }
          value={`${summary.runningServices}/${summary.attachedServices}`}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection label="API detail">
              {selected ? (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-foreground text-xs font-semibold">
                      {selected.name}
                    </span>
                    <StatusBadge state={selected.health.state} />
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs/relaxed">
                    {selected.summary ?? 'No API summary available.'}
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground text-xs">
                  {isLoading
                    ? 'Reading live runtime and service context.'
                    : 'Select an API contract to inspect runtime and service context.'}
                </p>
              )}
            </InspectorSection>
            {selected && (
              <InspectorSection label="Facts">
                <FactGrid>
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
                </FactGrid>
              </InspectorSection>
            )}
            {selected && (
              <InspectorSection label="Runtime service">
                <div className="border-y py-2">
                  <div className="flex items-start gap-2">
                    <HealthDot
                      className="mt-1"
                      state={
                        selectedService?.runtime_state ??
                        selectedService?.status
                      }
                    />
                    <span className="min-w-0">
                      <span className="text-foreground block text-xs">
                        {metadataDisplayText(selected, 'service_name')}
                      </span>
                      <span className="text-muted-foreground block font-mono text-[10px]">
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
                      </span>
                    </span>
                  </div>
                </div>
              </InspectorSection>
            )}
            {selected && (
              <InspectorSection label="Metadata">
                {platformMetadataRows(selected.metadata).length ? (
                  <FactGrid>
                    {platformMetadataRows(selected.metadata).map((row) => (
                      <Fact
                        key={row.label}
                        label={row.label}
                        value={row.value}
                      />
                    ))}
                  </FactGrid>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    No structured fields.
                  </p>
                )}
                {(result.error ?? servicesResult.error) && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    {result.error ?? servicesResult.error}
                  </p>
                )}
              </InspectorSection>
            )}
          </>
        }
        inspectorWidth="w-[24rem]"
        list={
          <SectionCard className="ring-0" title="API contracts">
            <div className="text-muted-foreground grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
              <span>Contract</span>
              <span>Backend</span>
              <span>Service</span>
              <span>Runtime</span>
              <span>State</span>
            </div>
            {isInitialLoading ? (
              <LoadingBlock className="p-3" label="Loading API contracts" />
            ) : contracts.length === 0 ? (
              <EmptyBlock
                description="The active stack has no API provider records to inspect."
                title="No API contracts configured"
              />
            ) : (
              <div className="divide-y divide-border">
                {contracts.map((contract) => (
                  <ApiRow
                    contract={contract}
                    key={contract.id}
                    onSelect={() => selectApi(contract.id)}
                    selected={contract.id === selected?.id}
                    service={serviceForContract(contract, services)}
                  />
                ))}
              </div>
            )}
          </SectionCard>
        }
      />
    </Page>
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
      className={cn(
        'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1fr)] items-center gap-3 px-3 py-2 text-left transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      onClick={onSelect}
      type="button"
    >
      <span className="text-foreground truncate text-xs font-medium">
        {contract.name}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {metadataDisplayText(contract, 'backend_kind')}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {metadataDisplayText(contract, 'service_name')}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {serviceRuntime(service)}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {contract.health.message ?? contract.health.state}
      </span>
    </button>
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
