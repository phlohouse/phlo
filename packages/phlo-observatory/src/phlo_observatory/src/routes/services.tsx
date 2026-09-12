/**
 * /services route. Stack service dashboard: start/stop/restart actions,
 * package installs, and per-service detail fetched directly after
 * mutations so stale cached state is never shown.
 */
import { createFileRoute } from '@tanstack/react-router'
import {
  Download,
  ExternalLink,
  Play,
  RotateCcw,
  Server,
  Square,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

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
import {
  invalidateCachedResources,
  loadCachedResource,
  useLiveResource,
} from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

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
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading ? 'Loading' : `${summary.running} running`}
          </Badge>
        }
        description="Active Docker services first, with optional service definitions separated from runtime health."
        title="Runtime services"
      />
      <StatGrid className="xl:grid-cols-4">
        <StatCard
          label="Running"
          state={summary.running ? 'ok' : 'unknown'}
          value={isLoading ? '—' : summary.running}
        />
        <StatCard
          label="Stack entries"
          value={isLoading ? '—' : summary.stackEntries}
        />
        <StatCard
          label="Setup complete"
          value={isLoading ? '—' : summary.setupJobs}
        />
        <StatCard
          label="Definitions"
          value={isLoading ? '—' : summary.definitions}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection label="Service detail">
              {selected ? (
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
              ) : (
                <p className="text-muted-foreground text-xs">
                  {isLoading
                    ? 'Reading live runtime state and actions.'
                    : 'Select a service to inspect runtime state and actions.'}
                </p>
              )}
            </InspectorSection>
            {pendingAction && (
              <InspectorSection label="Confirm service change">
                <p className="text-muted-foreground text-xs/relaxed whitespace-pre-wrap">
                  {pendingAction.message}
                </p>
                <div className="flex items-center gap-2 pt-2">
                  <Button
                    onClick={() => {
                      const action = pendingAction
                      setPendingAction(null)
                      if (action.type === 'action') runServiceAction(action.id)
                      else installPackage(action.packageName)
                    }}
                    size="sm"
                    type="button"
                  >
                    {pendingAction.type === 'install'
                      ? 'Install package'
                      : 'Run action'}
                  </Button>
                  <Button
                    onClick={() => setPendingAction(null)}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    Cancel
                  </Button>
                </div>
              </InspectorSection>
            )}
            {(actionMessage ??
              detail.error ??
              result.error ??
              directResult?.error) && (
              <InspectorSection label="Result">
                {actionMessage && (
                  <p className="text-muted-foreground font-mono text-[10px] break-all">
                    {actionMessage}
                  </p>
                )}
                {detail.error && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    {detail.error}
                  </p>
                )}
                {result.error && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    {result.error}
                  </p>
                )}
                {!result.error && directResult?.error && (
                  <p className="text-status-error font-mono text-[10px] break-all">
                    {directResult.error}
                  </p>
                )}
              </InspectorSection>
            )}
          </>
        }
        list={
          <div className="bg-sheet flex min-h-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
              <span className="text-foreground flex items-center gap-1.5 text-xs font-semibold">
                <Server className="text-muted-foreground size-3.5" />
                {serviceViewTitle(view)}
              </span>
              <div
                aria-label="Service view"
                className="border-input flex border"
                role="group"
              >
                {(
                  [
                    ['active', 'Active stack'],
                    ['definitions', 'Definitions'],
                    ['all', 'All'],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    className={cn(
                      'text-muted-foreground hover:bg-accent/50 px-2.5 py-1 text-[11px] transition-colors',
                      view === value && 'bg-accent text-foreground',
                    )}
                    data-active={view === value}
                    key={value}
                    onClick={() => setView(value)}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="text-muted-foreground grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_6rem_minmax(0,1.2fr)_5rem_minmax(0,1fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase max-lg:hidden">
              <span>Service</span>
              <span>Package</span>
              <span>Stack</span>
              <span>Health</span>
              <span>Role</span>
              <span>Links</span>
            </div>
            <ScrollArea className="min-h-0 flex-1">
              {visibleRows.map((service) => (
                <ServiceRow
                  key={service.id}
                  onSelect={() => selectService(service.id)}
                  selected={service.id === selected?.id}
                  service={service}
                />
              ))}
              {isLoading ? (
                <LoadingBlock
                  className="p-3"
                  label="Reading live Docker services and runtime definitions"
                />
              ) : (
                visibleRows.length === 0 && (
                  <EmptyBlock
                    className="py-10"
                    description="The active stack has no service records to inspect."
                    title="No services configured"
                  />
                )
              )}
            </ScrollArea>
          </div>
        }
      />
    </Page>
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
      className={cn(
        'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_6rem_minmax(0,1.2fr)_5rem_minmax(0,1fr)] items-center gap-3 border-b px-3 py-2 text-left transition-colors max-lg:grid-cols-[minmax(0,1fr)_6rem_minmax(0,1fr)]',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      data-active={selected}
      data-state={serviceState(service)}
      onClick={onSelect}
      type="button"
    >
      <span className="text-foreground flex items-center gap-2 truncate text-xs font-medium">
        <i
          className="status-dot flex-none"
          data-state={serviceDotState(service)}
        />
        {service.name}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px] max-lg:hidden">
        {servicePackageName(service) ?? 'native'}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {stackLabel(service)}
      </span>
      <span className="text-muted-foreground truncate text-[10px]">
        {serviceHealthLabel(service)}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px] max-lg:hidden">
        {service.kind}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px] max-lg:hidden">
        {service.links.length
          ? service.links.map((link) => link.label).join(', ')
          : 'none'}
      </span>
    </button>
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
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-foreground text-sm font-semibold">
          {service.name}
        </h2>
        <p className="text-muted-foreground mt-1 text-xs/relaxed">
          {serviceDescription(service)}
        </p>
      </div>
      <FactGrid>
        <Fact label="Stack" value={stackLabel(service)} />
        <Fact label="Status" value={service.status} />
        <Fact label="Runtime" value={serviceHealthLabel(service)} />
        <Fact label="Package" value={packageName ?? 'native'} />
      </FactGrid>
      {(visibleActions.length > 0 || canAddToStack || canInstallPackage) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {canAddToStack && (
            <Button
              onClick={() =>
                onAction(
                  addActionId,
                  `Add ${service.name} to this stack?\n\nThis will update the local service configuration.`,
                )
              }
              size="xs"
              type="button"
              variant="outline"
            >
              <Play className="size-3.5" />
              Add to stack
            </Button>
          )}
          {canInstallPackage && packageName && (
            <Button
              onClick={() => onInstall(packageName)}
              size="xs"
              type="button"
              variant="outline"
            >
              <Download className="size-3.5" />
              Install package
            </Button>
          )}
          {visibleActions.map((action) => (
            <Button
              disabled={!action.enabled}
              key={action.id}
              onClick={() => onAction(action.id)}
              size="xs"
              title={action.reason ?? undefined}
              type="button"
              variant="outline"
            >
              {iconForAction(action.kind)}
              {action.label}
            </Button>
          ))}
        </div>
      )}
      <div className="divide-border divide-y border-y">
        <ServiceFactRow
          detail={serviceRuntimeEvidence(service)}
          state={serviceState(service)}
          title="Runtime evidence"
        />
        <ServiceFactRow detail={dependencies} title="Dependencies" />
        <ServiceFactRow detail={dependents} title="Dependents" />
        <ServiceFactRow detail={ports} title="Ports" />
        <ServiceFactRow
          detail={`${detail?.config.length ?? 0} entries`}
          title="Config"
        />
        <ServiceFactRow
          detail={`${detail?.logs.length ?? 0} linked events`}
          title="Logs"
        />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {service.links.map((link) => (
          <a
            className="border-input hover:bg-accent inline-flex h-6 items-center gap-1 border px-2 font-mono text-[10px] transition-colors"
            href={link.url}
            key={`${link.kind}:${link.label}`}
          >
            <ExternalLink className="size-3" />
            {link.label}
          </a>
        ))}
        {service.links.length === 0 && (
          <span className="border-input text-muted-foreground inline-flex h-6 items-center gap-1 border px-2 font-mono text-[10px]">
            <Server className="size-3" />
            No links exposed
          </span>
        )}
      </div>
    </div>
  )
}

function ServiceFactRow({
  detail,
  state,
  title,
}: {
  detail: string
  state?: string
  title: string
}) {
  return (
    <div
      className="flex items-start justify-between gap-2 py-1.5"
      data-state={state}
    >
      <span className="text-foreground text-[11px]">{title}</span>
      <span className="text-muted-foreground text-right font-mono text-[10px] break-all">
        {detail}
      </span>
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
