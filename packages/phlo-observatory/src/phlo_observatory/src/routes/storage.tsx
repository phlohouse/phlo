/**
 * /storage route. Joins storage providers with runtime services and table
 * records so each provider can be inspected alongside its backing stack.
 */
import { createFileRoute } from '@tanstack/react-router'
import { Boxes, Database, HardDrive, Table2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

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
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'
import {
  formatPlatformMetadata,
  metadataDisplayText,
  platformMetadataRows,
  rawMetadataText,
} from '@/observatory/platformMetadata'

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
    <ObservatoryPage
      kicker="Platform"
      title="Storage"
      description="Registered table and object stores, active lakehouse services, and queryable table coverage."
      action={
        <span className="phlo-observatory-pill">
          {refreshState ? `${refreshState} · ` : ''}
          {providers.length} providers
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell phlo-observatory-storage-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-platform-summary">
            <StorageMetric
              icon={<Table2 className="size-4" />}
              label="Table stores"
              value={summary.tableStores}
            />
            <StorageMetric
              icon={<HardDrive className="size-4" />}
              label="Object stores"
              value={summary.objectStores}
            />
            <StorageMetric
              icon={<Boxes className="size-4" />}
              label="Runtime services"
              value={`${summary.runningServices}/${summary.runtimeServices}`}
            />
            <StorageMetric
              icon={<Database className="size-4" />}
              label="Queryable tables"
              value={`${summary.queryableTables}/${summary.tables}`}
            />
          </div>
          <div className="phlo-observatory-browser-toolbar">
            <span>
              <HardDrive className="size-4" />
              Storage providers
            </span>
            <span className="phlo-observatory-pill">
              {refreshState ? `${refreshState} · ` : ''}
              {providers.length} registered
            </span>
          </div>
          <div className="phlo-observatory-platform-table" role="table">
            <div className="phlo-observatory-platform-head" role="row">
              <span>Provider</span>
              <span>Capability</span>
              <span>System</span>
              <span>Compatibility</span>
              <span>State</span>
            </div>
            {providers.map((provider) => (
              <ProviderRow
                key={provider.id}
                onSelect={() => selectProvider(provider.id)}
                provider={provider}
                selected={provider.id === selected?.id}
              />
            ))}
            {isInitialLoading ? (
              <div className="phlo-observatory-run-provider-empty">
                <div>
                  <span className="phlo-observatory-inspector-label">
                    Storage
                  </span>
                  <h2>Loading storage providers</h2>
                  <p>
                    Reading live table stores, object stores, and runtime
                    services.
                  </p>
                </div>
              </div>
            ) : (
              providers.length === 0 && (
                <div className="phlo-observatory-run-provider-empty">
                  <div>
                    <span className="phlo-observatory-inspector-label">
                      Storage
                    </span>
                    <h2>No storage providers configured</h2>
                    <p>
                      The active stack has no storage provider records to
                      inspect.
                    </p>
                  </div>
                </div>
              )
            )}
          </div>
        </div>

        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">Storage detail</div>
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <p>{selected.summary ?? 'No provider summary available.'}</p>
              <dl className="phlo-observatory-facts">
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
              </dl>
              <div className="phlo-observatory-detail-list">
                {runtimeServices.map((service) => (
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
                  ? 'Checking storage detail'
                  : 'No provider selected'}
              </h2>
              <p>
                {isLoading
                  ? 'Reading live capability and service evidence.'
                  : 'Select a storage provider to inspect capability and service evidence.'}
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
          {tablesResult.error && (
            <div className="phlo-observatory-panel-footer">
              {tablesResult.error}
            </div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
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
      className="phlo-observatory-platform-row"
      data-active={selected}
      onClick={onSelect}
      role="row"
      type="button"
    >
      <span>{provider.name}</span>
      <span>{metadataDisplayText(provider, 'capability_type')}</span>
      <span>{storageSystem(provider)}</span>
      <span>{compatibilityLabel(provider)}</span>
      <span>{provider.health.message ?? provider.health.state}</span>
    </button>
  )
}

function StorageMetric({
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

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
