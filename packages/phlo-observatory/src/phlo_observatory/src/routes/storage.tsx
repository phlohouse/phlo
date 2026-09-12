/**
 * /storage route. Joins storage providers with runtime services and table
 * records so each provider can be inspected alongside its backing stack.
 */
import { createFileRoute } from '@tanstack/react-router'
import { Boxes, Database, HardDrive, Table2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ObservatoryService,
  ObservatorySurfaceItem,
  ObservatoryTable,
} from '@/observatory/api/types'
import {
  getObservatoryServices,
  getObservatoryStorageItems,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  formatPlatformMetadata,
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

export const Route = createFileRoute('/storage')({
  component: Storage,
})

const storageRuntimeServiceIds = ['minio', 'nessie', 'trino']

export function Storage() {
  const result = useLiveResource(
    getObservatoryStorageItems,
    120_000,
    'observatory:storage',
  )
  const servicesResult = useLiveResource(
    getObservatoryServices,
    120_000,
    'observatory:services',
  )
  const tablesResult = useLiveResource(
    getObservatoryTableRecords,
    120_000,
    'observatory:tables',
  )
  const providers = result.data ?? []
  const services = servicesResult.data ?? []
  const tables = tablesResult.data ?? []
  const isLoading =
    result.isLoading || servicesResult.isLoading || tablesResult.isLoading
  const isInitialLoading = result.isLoading && providers.length === 0
  const refreshState = isLoading
    ? isInitialLoading
      ? 'checking'
      : 'refreshing'
    : null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected =
    providers.find((provider) => provider.id === selectedId) ??
    providers.find(
      (provider) => rawMetadataText(provider, 'provider') === 'iceberg',
    ) ??
    providers[0] ??
    null
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
    () => summarizeStorage(providers, services, tables),
    [providers, services, tables],
  )
  const runtimeServices = storageRuntimeServices(services)

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'providerId',
    )
    if (!requested || requested === selectedId) return
    if (providers.some((provider) => provider.id === requested)) {
      setSelectedId(requested)
    }
  }, [providers, selectedId])

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
            {providers.length} providers
          </Badge>
        }
        description="Registered table and object stores, active lakehouse services, and queryable table coverage."
        title="Storage"
      />
      <StatGrid>
        <StatCard
          icon={<Table2 className="size-3.5" />}
          label="Table stores"
          value={summary.tableStores}
        />
        <StatCard
          icon={<HardDrive className="size-3.5" />}
          label="Object stores"
          value={summary.objectStores}
        />
        <StatCard
          icon={<Boxes className="size-3.5" />}
          label="Runtime services"
          state={
            summary.runtimeServices > 0 &&
            summary.runningServices < summary.runtimeServices
              ? 'warning'
              : 'ok'
          }
          value={`${summary.runningServices}/${summary.runtimeServices}`}
        />
        <StatCard
          icon={<Database className="size-3.5" />}
          label="Queryable tables"
          value={`${summary.queryableTables}/${summary.tables}`}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection label="Storage detail">
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
                    ? 'Reading live capability and service evidence.'
                    : 'Select a storage provider to inspect capability and service evidence.'}
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
                    label="Capability"
                    value={metadataDisplayText(selected, 'capability_type')}
                  />
                  <Fact label="System" value={storageSystem(selected)} />
                </FactGrid>
              </InspectorSection>
            )}
            {selected && (
              <InspectorSection label="Runtime services">
                <div className="divide-y divide-border border-y">
                  {runtimeServices.map((service) => (
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
                {(result.error ??
                  servicesResult.error ??
                  tablesResult.error) && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    {result.error ?? servicesResult.error ?? tablesResult.error}
                  </p>
                )}
              </InspectorSection>
            )}
          </>
        }
        inspectorWidth="w-[24rem]"
        list={
          <SectionCard className="ring-0" title="Storage providers">
            <div className="text-muted-foreground grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
              <span>Provider</span>
              <span>Capability</span>
              <span>System</span>
              <span>Compatibility</span>
              <span>State</span>
            </div>
            {isInitialLoading ? (
              <LoadingBlock className="p-3" label="Loading storage providers" />
            ) : providers.length === 0 ? (
              <EmptyBlock
                description="The active stack has no storage provider records to inspect."
                title="No storage providers configured"
              />
            ) : (
              <div className="divide-y divide-border">
                {providers.map((provider) => (
                  <ProviderRow
                    key={provider.id}
                    onSelect={() => selectProvider(provider.id)}
                    provider={provider}
                    selected={provider.id === selected?.id}
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
  onSelect,
  provider,
  selected,
}: {
  onSelect: () => void
  provider: ObservatorySurfaceItem
  selected: boolean
}) {
  return (
    <button
      className={cn(
        'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1fr)] items-center gap-3 px-3 py-2 text-left transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      onClick={onSelect}
      type="button"
    >
      <span className="text-foreground truncate text-xs font-medium">
        {provider.name}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {metadataDisplayText(provider, 'capability_type')}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {storageSystem(provider)}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {compatibilityLabel(provider)}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {provider.health.message ?? provider.health.state}
      </span>
    </button>
  )
}

function summarizeStorage(
  providers: Array<ObservatorySurfaceItem>,
  services: Array<ObservatoryService>,
  tables: Array<ObservatoryTable>,
): {
  tableStores: number
  objectStores: number
  runtimeServices: number
  runningServices: number
  tables: number
  queryableTables: number
} {
  const runtimeServices = storageRuntimeServices(services)
  return {
    tableStores: providers.filter(
      (provider) =>
        rawMetadataText(provider, 'capability_type') === 'table_store',
    ).length,
    objectStores: providers.filter(
      (provider) =>
        rawMetadataText(provider, 'capability_type') === 'object_store',
    ).length,
    runtimeServices: runtimeServices.length,
    runningServices: runtimeServices.filter(
      (service) => (service.runtime_state ?? service.status) === 'running',
    ).length,
    tables: tables.length,
    queryableTables: tables.filter(isQueryableTable).length,
  }
}

function storageRuntimeServices(
  services: Array<ObservatoryService>,
): Array<ObservatoryService> {
  return services.filter((service) =>
    storageRuntimeServiceIds.includes(service.id),
  )
}

function isQueryableTable(table: ObservatoryTable): boolean {
  const state = table.metadata.catalog_state
  if (state === 'queryable') return true
  if (state === 'model_only') return false
  return table.metadata.catalog_present === true
}

function storageSystem(provider: ObservatorySurfaceItem): string {
  for (const key of ['storage_system', 'type', 'provider']) {
    const value = rawMetadataText(provider, key)
    if (value !== 'not reported') return formatPlatformMetadata(value)
  }
  return 'not reported'
}

function compatibilityLabel(provider: ObservatorySurfaceItem): string {
  const compatibility = provider.metadata.compatibility
  if (
    typeof compatibility === 'object' &&
    compatibility !== null &&
    'target' in compatibility
  ) {
    return formatPlatformMetadata(compatibility.target)
  }
  return 'not reported'
}
