/**
 * /observability route. Observability providers joined with service health;
 * the selection is persisted through ?providerId.
 */
import { createFileRoute } from '@tanstack/react-router'
import { Activity, Bell, Database, Radio } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ObservatoryService,
  ObservatorySurfaceItem,
} from '@/observatory/api/types'
import {
  getObservatoryObservabilityItems,
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
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {refreshState ? `${refreshState} · ` : ''}
            {items.length} providers
          </Badge>
        }
        description="Telemetry provider registration, expected backend, service dependencies, and alerting coverage."
        title="Observability"
      />
      <StatGrid>
        <StatCard
          icon={<Radio className="size-3.5" />}
          label="Registered"
          value={summary.registered}
        />
        <StatCard
          icon={<Activity className="size-3.5" />}
          label="Backends"
          value={summary.backends}
        />
        <StatCard
          icon={<Database className="size-3.5" />}
          label="Services running"
          state={
            summary.requiredDeps > 0 &&
            summary.runningDeps < summary.requiredDeps
              ? 'warning'
              : 'ok'
          }
          value={`${summary.runningDeps}/${summary.requiredDeps}`}
        />
        <StatCard
          icon={<Bell className="size-3.5" />}
          label="Alert sinks"
          value={summary.alertSinks}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection label="Provider detail">
              {selected ? (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-foreground text-xs font-semibold">
                      {selected.name}
                    </span>
                    <StatusBadge state={selected.health.state} />
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs/relaxed">
                    {selected.summary ?? 'No provider summary available.'}
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground text-xs">
                  {isLoading
                    ? 'Reading live telemetry and service dependency evidence.'
                    : 'Select a provider to inspect telemetry and service evidence.'}
                </p>
              )}
            </InspectorSection>
            {selected && (
              <InspectorSection label="Facts">
                <FactGrid>
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
                </FactGrid>
              </InspectorSection>
            )}
            {selected && (
              <InspectorSection label="Service dependencies">
                {selectedDependencies.length ? (
                  <div className="divide-y divide-border border-y">
                    {selectedDependencies.map((service) => (
                      <div
                        className="flex items-start gap-2 py-2"
                        key={service.id}
                      >
                        <HealthDot
                          className="mt-1"
                          state={service.runtime_state ?? service.status}
                        />
                        <span className="min-w-0">
                          <span className="text-foreground block text-xs">
                            {service.name}
                          </span>
                          <span className="text-muted-foreground block font-mono text-[10px]">
                            {[
                              service.runtime_state ?? service.status,
                              service.in_stack ? 'in stack' : 'not in stack',
                              service.health.message,
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                          </span>
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    No required services declared.
                  </p>
                )}
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
          <SectionCard className="ring-0" title="Providers">
            <div className="text-muted-foreground grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
              <span>Provider</span>
              <span>Capability</span>
              <span>Required services</span>
              <span>State</span>
            </div>
            {isInitialLoading ? (
              <LoadingBlock className="p-3" label="Loading providers" />
            ) : items.length === 0 ? (
              <EmptyBlock
                description="The active stack has no telemetry or alerting provider records to inspect."
                title="No observability providers configured"
              />
            ) : (
              <div className="divide-y divide-border">
                {items.map((item) => (
                  <ProviderRow
                    item={item}
                    key={item.id}
                    onSelect={() => selectProvider(item.id)}
                    selected={item.id === selected?.id}
                    services={services}
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
      className={cn(
        'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] items-center gap-3 px-3 py-2 text-left transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      onClick={onSelect}
      type="button"
    >
      <span className="text-foreground truncate text-xs font-medium">
        {item.name}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {metadataDisplayText(item, 'capability_type')}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {dependencies.length
          ? `${running}/${dependencies.length} running`
          : 'no dependency declared'}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {item.health.message ?? item.health.state}
      </span>
    </button>
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
