/**
 * /services route. Stack service dashboard: start/stop/restart actions,
 * package installs, and per-service detail fetched directly after
 * mutations so stale cached state is never shown.
 */
import { createFileRoute } from '@tanstack/react-router'
import {
  Download,
  ExternalLink,
  Package,
  Play,
  Radio,
  RotateCcw,
  Server,
  Square,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryResourceResult,
  ObservatoryService,
  ObservatoryServiceDetail,
} from '@/observatory/api/types'
import {
  getObservatoryServiceDetail,
  getObservatoryServiceDetailDirect,
  getObservatoryServices,
  getObservatoryServicesDirect,
  installObservatoryPackage,
  installObservatoryPackageDirect,
  runObservatoryAction,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  invalidateCachedResources,
  loadCachedResource,
  useLiveResource,
} from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/services')({
  component: Services,
})

type ServiceSummary = {
  running: number
  stackEntries: number
  setupJobs: number
  attention: number
  definitions: number
}

type ServiceView = 'active' | 'definitions' | 'all'
type PendingServiceAction =
  | {
      type: 'action'
      id: string
      message: string
    }
  | {
      type: 'install'
      packageName: string
      message: string
    }

export function Services() {
  const result = useLiveResource(
    getObservatoryServices,
    120_000,
    'observatory:services',
  )
  const [directResult, setDirectResult] = useState<ObservatoryResourceResult<
    Array<ObservatoryService>
  > | null>(null)
  const apiServices = result.data ?? []
  const directServices = directResult?.data ?? []
  const isLoading =
    result.isLoading ||
    (apiServices.length === 0 && directResult === null && !result.error)
  const services =
    directServices.length > 0 || apiServices.length === 0
      ? directServices
      : apiServices
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [pendingAction, setPendingAction] =
    useState<PendingServiceAction | null>(null)
  const [view, setView] = useState<ServiceView>('active')
  const rows = useMemo(() => sortServices(services), [services])
  const visibleRows = useMemo(
    () => rows.filter((service) => serviceVisibleInView(service, view)),
    [rows, view],
  )
  const selected =
    rows.find((service) => service.id === selectedId) ??
    rows.find((service) => service.in_stack && service.status === 'running') ??
    rows[0] ??
    null
  const summary = useMemo(() => summarizeServices(services), [services])
  const [detail, setDetail] = useState<
    ObservatoryResourceResult<ObservatoryServiceDetail>
  >({
    data: null,
    error: null,
  })

  const selectService = useCallback((serviceId: string) => {
    setSelectedId(serviceId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('serviceId', serviceId)
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [])

  const runServiceAction = useCallback((actionId: string) => {
    setActionMessage('Running action...')
    void runObservatoryActionDirect({ actionId }).then(async (next) => {
      if (!next.data && next.error) {
        next = await runObservatoryAction({
          data: { actionId },
        }).catch((error: unknown) => ({
          data: null,
          error: error instanceof Error ? error.message : 'Action failed',
        }))
      }
      invalidateCachedResources([
        'observatory:operations',
        'observatory:services',
      ])
      setActionMessage(next.data?.message ?? next.error ?? 'Action completed')
    })
  }, [])

  const installPackage = useCallback((packageName: string) => {
    setActionMessage(`Installing ${packageName}...`)
    void installObservatoryPackageDirect({ packageName }).then(async (next) => {
      if (!next.data && next.error) {
        next = await installObservatoryPackage({
          data: { packageName },
        }).catch((error: unknown) => ({
          data: null,
          error: error instanceof Error ? error.message : 'Install failed',
        }))
      }
      invalidateCachedResources([
        'observatory:capabilities',
        'observatory:operations',
        'observatory:services',
      ])
      setActionMessage(next.data?.message ?? next.error ?? 'Install completed')
    })
  }, [])

  useEffect(() => {
    if (directResult || (result.data && result.data.length > 0)) return
    let cancelled = false
    void getObservatoryServicesDirect().then((next) => {
      if (!cancelled) setDirectResult(next)
    })
    return () => {
      cancelled = true
    }
  }, [directResult, result.data])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'serviceId',
    )
    if (!requested || requested === selectedId) return

    const requestedService = rows.find((service) => service.id === requested)
    if (requestedService && serviceVisibleInView(requestedService, view)) {
      setSelectedId(requested)
      return
    }

    const fallback = visibleRows[0]?.id
    if (!fallback || fallback === selectedId) return
    setSelectedId(fallback)
    const url = new URL(window.location.href)
    url.searchParams.set('serviceId', fallback)
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [rows, selectedId, view, visibleRows])

  useEffect(() => {
    if (visibleRows.length === 0) return
    if (selected && visibleRows.some((service) => service.id === selected.id)) {
      return
    }
    const fallback = visibleRows[0].id
    setSelectedId(fallback)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('serviceId', fallback)
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [selected, visibleRows])

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    void loadCachedResource(
      `observatory:service-detail:${selected.id}`,
      async () => {
        const directResponse = await getObservatoryServiceDetailDirect({
          serviceId: selected.id,
        })
        if (directResponse.data || !directResponse.error) return directResponse
        const response = await getObservatoryServiceDetail({
          data: { serviceId: selected.id },
        }).catch((error: unknown) => ({
          data: null,
          error:
            error instanceof Error
              ? error.message
              : 'Lakehouse API is unavailable',
        }))
        if (response.data || !response.error) return response
        return directResponse
      },
      { staleMs: 120_000 },
    ).then((next) => {
      if (!cancelled) setDetail(next)
    })
    return () => {
      cancelled = true
    }
  }, [selected])

  return (
    <ObservatoryPage
      kicker="Services"
      title="Runtime services"
      description="Active Docker services first, with optional service definitions separated from runtime health."
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${summary.running} running`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell phlo-observatory-services-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-platform-summary">
            <PlatformMetric
              icon={<Radio className="size-4" />}
              label="Running"
              value={isLoading ? 'Loading' : summary.running}
            />
            <PlatformMetric
              icon={<Server className="size-4" />}
              label="Stack entries"
              value={isLoading ? 'Loading' : summary.stackEntries}
            />
            <PlatformMetric
              icon={<Play className="size-4" />}
              label="Setup complete"
              value={isLoading ? 'Loading' : summary.setupJobs}
            />
            <PlatformMetric
              icon={<Package className="size-4" />}
              label="Definitions"
              value={isLoading ? 'Loading' : summary.definitions}
            />
          </div>
          <div className="phlo-observatory-browser-toolbar">
            <span>
              <Server className="size-4" />
              {serviceViewTitle(view)}
            </span>
            <div
              className="phlo-observatory-service-view-toggle"
              role="group"
              aria-label="Service view"
            >
              <button
                data-active={view === 'active'}
                onClick={() => setView('active')}
                type="button"
              >
                Active stack
              </button>
              <button
                data-active={view === 'definitions'}
                onClick={() => setView('definitions')}
                type="button"
              >
                Definitions
              </button>
              <button
                data-active={view === 'all'}
                onClick={() => setView('all')}
                type="button"
              >
                All
              </button>
            </div>
          </div>
          <div
            className="phlo-observatory-platform-table phlo-observatory-services-table"
            role="table"
          >
            <div className="phlo-observatory-platform-head" role="row">
              <span>Service</span>
              <span>Package</span>
              <span>Stack</span>
              <span>Health</span>
              <span>Role</span>
              <span>Links</span>
            </div>
            {visibleRows.map((service) => (
              <ServiceRow
                key={service.id}
                onSelect={() => selectService(service.id)}
                selected={service.id === selected?.id}
                service={service}
              />
            ))}
            {isLoading ? (
              <div className="phlo-observatory-run-provider-empty">
                <div>
                  <span className="phlo-observatory-inspector-label">
                    Service inventory
                  </span>
                  <h2>Loading services</h2>
                  <p>Reading live Docker services and runtime definitions.</p>
                </div>
              </div>
            ) : (
              visibleRows.length === 0 && (
                <div className="phlo-observatory-run-provider-empty">
                  <div>
                    <span className="phlo-observatory-inspector-label">
                      Service inventory
                    </span>
                    <h2>No services configured</h2>
                    <p>The active stack has no service records to inspect.</p>
                  </div>
                </div>
              )
            )}
          </div>
        </div>

        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">Service detail</div>
          {selected ? (
            <>
              <ServiceDetail
                detail={detail.data}
                onAction={(actionId, confirmationMessage) => {
                  setPendingAction({
                    type: 'action',
                    id: actionId,
                    message:
                      confirmationMessage ??
                      `Run ${actionId}. This calls phlo-api to change a local service.`,
                  })
                }}
                onInstall={(packageName) => {
                  setPendingAction({
                    type: 'install',
                    packageName,
                    message: `Install ${packageName}. This modifies the Python environment used by phlo-api.`,
                  })
                }}
                service={selected}
              />
              {pendingAction && (
                <ServiceActionConfirm
                  action={pendingAction}
                  onCancel={() => setPendingAction(null)}
                  onConfirm={() => {
                    const action = pendingAction
                    setPendingAction(null)
                    if (action.type === 'action') runServiceAction(action.id)
                    else installPackage(action.packageName)
                  }}
                />
              )}
            </>
          ) : (
            <>
              <h2>
                {isLoading ? 'Loading service detail' : 'No service selected'}
              </h2>
              <p>
                {isLoading
                  ? 'Reading live runtime state and actions.'
                  : 'Select a service to inspect runtime state and actions.'}
              </p>
            </>
          )}
          {actionMessage && (
            <div className="phlo-observatory-panel-footer">{actionMessage}</div>
          )}
          {detail.error && (
            <div className="phlo-observatory-panel-footer">{detail.error}</div>
          )}
          {result.error && (
            <div className="phlo-observatory-panel-footer">{result.error}</div>
          )}
          {!result.error && directResult?.error && (
            <div className="phlo-observatory-panel-footer">
              {directResult.error}
            </div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function ServiceRow({
  onSelect,
  selected,
  service,
}: {
  onSelect: () => void
  selected: boolean
  service: ObservatoryService
}) {
  return (
    <button
      className="phlo-observatory-platform-row phlo-observatory-service-inventory-row"
      data-active={selected}
      data-state={serviceState(service)}
      onClick={onSelect}
      role="row"
      type="button"
    >
      <span>
        <i
          className="phlo-observatory-dot"
          data-state={serviceDotState(service)}
        />
        {service.name}
      </span>
      <span>{servicePackageName(service) ?? 'native'}</span>
      <span>{stackLabel(service)}</span>
      <span>{serviceHealthLabel(service)}</span>
      <span>{service.kind}</span>
      <span>
        {service.links.length
          ? service.links.map((link) => link.label).join(', ')
          : 'none'}
      </span>
    </button>
  )
}

function ServiceActionConfirm({
  action,
  onCancel,
  onConfirm,
}: {
  action: PendingServiceAction
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="phlo-observatory-service-confirm">
      <div>
        <span className="phlo-observatory-inspector-label">
          Confirm service change
        </span>
        <p>{action.message}</p>
      </div>
      <div className="phlo-observatory-inline-actions">
        <button onClick={onConfirm} type="button">
          {action.type === 'install' ? 'Install package' : 'Run action'}
        </button>
        <button onClick={onCancel} type="button">
          Cancel
        </button>
      </div>
    </div>
  )
}

function ServiceDetail({
  detail,
  onAction,
  onInstall,
  service,
}: {
  detail: ObservatoryServiceDetail | null
  onAction: (actionId: string, confirmationMessage?: string) => void
  onInstall: (packageName: string) => void
  service: ObservatoryService
}) {
  const actions = serviceActionsForDetail(service, detail)
  const packageName = servicePackageName(service)
  const packageInstalled = service.metadata.package_installed !== false
  const canAddToStack = !service.in_stack && packageInstalled
  const canInstallPackage = !packageInstalled && Boolean(packageName)
  const addActionId = `${service.id}:add`
  const visibleActions = canAddToStack
    ? actions.filter((action) => action.id !== addActionId)
    : actions
  const dependencies =
    detail?.dependencies.map((item) => item.name).join(', ') || 'none'
  const dependents =
    detail?.dependents.map((item) => item.name).join(', ') || 'none'
  const ports =
    detail?.ports
      .map((port) => [port.published, port.target].filter(Boolean).join(' -> '))
      .join(', ') || 'none'

  return (
    <>
      <h2>{service.name}</h2>
      <p>{serviceDescription(service)}</p>
      <dl className="phlo-observatory-facts">
        <Fact label="Stack" value={stackLabel(service)} />
        <Fact label="Status" value={service.status} />
        <Fact label="Runtime" value={serviceHealthLabel(service)} />
        <Fact label="Package" value={packageName ?? 'native'} />
      </dl>
      {(visibleActions.length > 0 || canAddToStack || canInstallPackage) && (
        <div className="phlo-observatory-action-row">
          {canAddToStack && (
            <button
              onClick={() =>
                onAction(
                  addActionId,
                  `Add ${service.name} to this stack?\n\nThis will update the local service configuration.`,
                )
              }
              type="button"
            >
              <Play className="size-3.5" />
              Add to stack
            </button>
          )}
          {canInstallPackage && packageName && (
            <button onClick={() => onInstall(packageName)} type="button">
              <Download className="size-3.5" />
              Install package
            </button>
          )}
          {visibleActions.map((action) => (
            <button
              disabled={!action.enabled}
              key={action.id}
              onClick={() => onAction(action.id)}
              title={action.reason ?? undefined}
              type="button"
            >
              {iconForAction(action.kind)}
              {action.label}
            </button>
          ))}
        </div>
      )}
      <div className="phlo-observatory-detail-list">
        <div
          className="phlo-observatory-mini-row"
          data-state={serviceState(service)}
        >
          <span>Runtime evidence</span>
          <small>{serviceRuntimeEvidence(service)}</small>
        </div>
        <div className="phlo-observatory-mini-row">
          <span>Dependencies</span>
          <small>{dependencies}</small>
        </div>
        <div className="phlo-observatory-mini-row">
          <span>Dependents</span>
          <small>{dependents}</small>
        </div>
        <div className="phlo-observatory-mini-row">
          <span>Ports</span>
          <small>{ports}</small>
        </div>
        <div className="phlo-observatory-mini-row">
          <span>Config</span>
          <small>{detail?.config.length ?? 0} entries</small>
        </div>
        <div className="phlo-observatory-mini-row">
          <span>Logs</span>
          <small>{detail?.logs.length ?? 0} linked events</small>
        </div>
      </div>
      <div className="phlo-observatory-chip-cloud">
        {service.links.map((link) => (
          <a
            className="phlo-observatory-chip"
            href={link.url}
            key={`${link.kind}:${link.label}`}
          >
            <ExternalLink className="size-3" />
            {link.label}
          </a>
        ))}
        {service.links.length === 0 && (
          <span className="phlo-observatory-chip">
            <Server className="size-3" />
            No links exposed
          </span>
        )}
      </div>
    </>
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

function summarizeServices(
  services: Array<ObservatoryService>,
): ServiceSummary {
  const stackEntries = services.filter((service) => service.in_stack)
  return {
    running: stackEntries.filter((service) => service.status === 'running')
      .length,
    stackEntries: stackEntries.length,
    setupJobs: stackEntries.filter(isCompletedSetup).length,
    attention: stackEntries.filter(needsAttention).length,
    definitions: services.filter((service) => !service.in_stack).length,
  }
}

function sortServices(
  services: Array<ObservatoryService>,
): Array<ObservatoryService> {
  return services.slice().sort((left, right) => {
    const leftRank = serviceSortRank(left)
    const rightRank = serviceSortRank(right)
    if (leftRank !== rightRank) return leftRank - rightRank
    return left.name.localeCompare(right.name)
  })
}

function serviceSortRank(service: ObservatoryService): number {
  if (service.in_stack && service.status === 'running') return 0
  if (isCompletedSetup(service)) return 1
  if (service.in_stack) return 2
  if (service.metadata.installable === true) return 4
  return 3
}

function isCompletedSetup(service: ObservatoryService): boolean {
  return (
    service.in_stack === true &&
    service.status === 'stopped' &&
    service.health.state === 'ok'
  )
}

function needsAttention(service: ObservatoryService): boolean {
  if (service.status === 'unhealthy') return true
  if (service.health.state === 'warning' || service.health.state === 'error')
    return true
  return (
    service.in_stack === true &&
    service.status === 'stopped' &&
    !isCompletedSetup(service)
  )
}

function stackLabel(service: ObservatoryService): string {
  if (isCompletedSetup(service)) return 'setup complete'
  if (service.in_stack) return 'in stack'
  if (service.metadata.installable === true) return 'not installed'
  return 'definition only'
}

function serviceVisibleInView(
  service: ObservatoryService,
  view: ServiceView,
): boolean {
  if (view === 'active') return service.in_stack === true
  if (view === 'definitions') return service.in_stack !== true
  return true
}

function serviceViewTitle(view: ServiceView): string {
  if (view === 'active') return 'Active stack'
  if (view === 'definitions') return 'Optional definitions'
  return 'Service inventory'
}

function serviceState(service: ObservatoryService): string {
  if (needsAttention(service)) return 'error'
  if (isCompletedSetup(service)) return 'ok'
  if (service.in_stack) return service.status
  return 'unknown'
}

function serviceDotState(service: ObservatoryService): string {
  if (service.in_stack && service.status === 'running') return 'ok'
  return serviceState(service)
}

function servicePackageName(service: ObservatoryService): string | null {
  return typeof service.metadata.package === 'string'
    ? service.metadata.package
    : null
}

function serviceDescription(service: ObservatoryService): string {
  if (service.in_stack) {
    return (
      service.health.message ??
      'Runtime evidence is available for this service.'
    )
  }
  if (
    typeof service.metadata.description === 'string' &&
    service.metadata.description
  ) {
    return service.metadata.description
  }
  return 'Optional service definition available to this lakehouse.'
}

function serviceHealthLabel(service: ObservatoryService): string {
  if (!service.in_stack) {
    return service.metadata.installable === true
      ? 'package available'
      : 'not in active stack'
  }
  return service.health.message ?? service.health.state
}

function serviceRuntimeEvidence(service: ObservatoryService): string {
  if (!service.in_stack) {
    return 'Definition is available, but this service is not part of the active Docker stack.'
  }
  return service.health.message ?? 'No runtime message available.'
}

function serviceActionsForDetail(
  service: ObservatoryService,
  detail: ObservatoryServiceDetail | null,
) {
  const actions = detail?.actions ?? []
  if (
    service.in_stack ||
    actions.some((action) => action.id === `${service.id}:add`)
  ) {
    return actions
  }
  const packageInstalled = service.metadata.package_installed !== false
  return [
    ...actions,
    {
      id: `${service.id}:add`,
      label: 'Add to stack',
      kind: 'service.add',
      enabled: packageInstalled,
      requires_confirmation: true,
      reason: packageInstalled
        ? null
        : `Install ${servicePackageName(service) ?? service.name} before adding it to the stack.`,
      risk_level: 'low' as const,
      required_capability: null,
      required_service: null,
      required_permission: null,
      equivalent_cli_command: `phlo services add ${service.id}`,
      expected_evidence: [],
      background_operation_id: null,
    },
  ]
}

function iconForAction(kind: string) {
  if (kind.endsWith('stop')) return <Square className="size-3.5" />
  if (kind.endsWith('restart')) return <RotateCcw className="size-3.5" />
  return <Play className="size-3.5" />
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
