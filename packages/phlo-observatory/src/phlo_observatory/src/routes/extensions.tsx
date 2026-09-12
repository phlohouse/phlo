/**
 * /extensions route. Lists installed extensions and fetches the selected
 * one's contributed routes and nav entries; parent layout for
 * /extensions/$extensionId.
 */
import {
  Link,
  Outlet,
  createFileRoute,
  useMatches,
} from '@tanstack/react-router'
import { Navigation, Plug, Route as RouteIcon, Settings } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ObservatoryExtension,
  ObservatoryExtensionDetail,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import {
  getObservatoryExtensionDetailDirect,
  getObservatoryExtensions,
} from '@/observatory/api/resources'
import { useLiveResource } from '@/observatory/routes/liveResource'
import { labelValue, metadataDisplayText } from '@/observatory/platformMetadata'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { StatusBadge } from '@/components/observatory/status'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/extensions')({
  component: Extensions,
})

export function Extensions() {
  const matches = useMatches()
  const result = useLiveResource(getObservatoryExtensions)
  const extensions = result.data ?? []
  const isLoading = result.isLoading
  const isInitialLoading = isLoading && extensions.length === 0
  const refreshState = isLoading
    ? isInitialLoading
      ? 'checking'
      : 'refreshing'
    : null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected =
    extensions.find((extension) => extension.id === selectedId) ??
    extensions[0] ??
    null
  const selectExtension = useCallback((extensionId: string) => {
    setSelectedId(extensionId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('extensionId', extensionId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])
  const [detail, setDetail] = useState<
    ObservatoryResourceResult<ObservatoryExtensionDetail>
  >({
    data: null,
    error: null,
  })
  const summary = useMemo(() => summarizeExtensions(extensions), [extensions])

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    setDetail({ data: null, error: null })
    void getObservatoryExtensionDetailDirect({
      extensionId: selected.id,
    }).then((next) => {
      if (!cancelled) setDetail(next)
    })
    return () => {
      cancelled = true
    }
  }, [selected])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'extensionId',
    )
    if (!requested || requested === selectedId) return
    if (extensions.some((extension) => extension.id === requested)) {
      setSelectedId(requested)
    }
  }, [extensions, selectedId])

  useEffect(() => {
    if (selectedId !== null || !selected) return
    setSelectedId(selected.id)
  }, [selected, selectedId])

  if (matches.some((match) => match.routeId === '/extensions/$extensionId')) {
    return <Outlet />
  }

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {refreshState ? `${refreshState} · ` : ''}
            {extensions.length} installed
          </Badge>
        }
        description="Installed Observatory providers, declared navigation targets, settings scopes, and manifest coverage."
        title="Extension registry"
      />
      <StatGrid>
        <StatCard
          icon={<Plug className="size-3.5" />}
          label="Installed"
          value={summary.installed}
        />
        <StatCard
          icon={<Navigation className="size-3.5" />}
          label="Nav entries"
          value={summary.navEntries}
        />
        <StatCard
          icon={<RouteIcon className="size-3.5" />}
          label="Routes"
          value={summary.routes}
        />
        <StatCard
          icon={<Settings className="size-3.5" />}
          label="Settings scopes"
          value={summary.settingsScopes}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection label="Extension detail">
              {selected ? (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-foreground text-xs font-semibold">
                      {selected.name}
                    </span>
                    <StatusBadge
                      label={selected.enabled ? 'enabled' : 'disabled'}
                      state={selected.enabled ? 'ok' : 'unknown'}
                    />
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs/relaxed">
                    {extensionSummary(selected)}
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground text-xs">
                  {isLoading
                    ? 'Reading extension manifest and contribution details.'
                    : 'Select an extension to inspect manifest and contribution details.'}
                </p>
              )}
            </InspectorSection>
            {selected && (
              <InspectorSection label="Facts">
                <FactGrid>
                  <Fact
                    label="State"
                    value={selected.enabled ? 'enabled' : 'disabled'}
                  />
                  <Fact
                    label="Version"
                    value={selected.version ?? 'not reported'}
                  />
                  <Fact
                    label="Settings"
                    value={
                      selected.settings_scope
                        ? labelValue(selected.settings_scope)
                        : 'none'
                    }
                  />
                  <Fact
                    label="Plugin"
                    value={metadataDisplayText(selected, 'plugin')}
                  />
                </FactGrid>
              </InspectorSection>
            )}
            {selected && (
              <InspectorSection label="Navigation">
                <div className="divide-border divide-y border-y">
                  {contributedNav(selected).map((navItem) => (
                    <Link
                      className="hover:bg-accent/50 flex items-center justify-between gap-2 py-1.5 transition-colors"
                      key={navItem}
                      to={navItem}
                    >
                      <span className="text-foreground font-mono text-[11px]">
                        {navItem}
                      </span>
                      <span className="text-muted-foreground font-mono text-[10px]">
                        navigation target
                      </span>
                    </Link>
                  ))}
                  {contributedNav(selected).length === 0 && (
                    <div className="flex items-center justify-between gap-2 py-1.5">
                      <span className="text-foreground text-[11px]">
                        Navigation
                      </span>
                      <span className="text-muted-foreground font-mono text-[10px]">
                        No navigation entry declared
                      </span>
                    </div>
                  )}
                </div>
              </InspectorSection>
            )}
            {selected && (
              <InspectorSection label="Contributions">
                <div className="divide-border divide-y border-y">
                  {(detail.data?.routes ?? selected.routes).map((route) => (
                    <div
                      className="flex items-center justify-between gap-2 py-1.5"
                      key={route}
                    >
                      <span className="text-foreground font-mono text-[11px]">
                        {contributedRoute(route)}
                      </span>
                      <span className="text-muted-foreground font-mono text-[10px]">
                        route
                      </span>
                    </div>
                  ))}
                  {detail.data && detail.data.capabilities.length > 0
                    ? detail.data.capabilities.map((capability) => (
                        <div
                          className="flex items-center justify-between gap-2 py-1.5"
                          key={capability.id}
                        >
                          <span className="text-foreground text-[11px]">
                            {capability.label}
                          </span>
                          <span className="text-muted-foreground font-mono text-[10px]">
                            {labelValue(capability.kind)}
                          </span>
                        </div>
                      ))
                    : null}
                  {detail.data && detail.data.routes.length === 0 && (
                    <div className="flex items-center justify-between gap-2 py-1.5">
                      <span className="text-foreground text-[11px]">
                        Routes
                      </span>
                      <span className="text-muted-foreground font-mono text-[10px]">
                        No extension routes declared
                      </span>
                    </div>
                  )}
                </div>
                {(detail.error ?? result.error) && (
                  <p className="text-status-error mt-2 font-mono text-[10px] break-all">
                    {detail.error ?? result.error}
                  </p>
                )}
              </InspectorSection>
            )}
          </>
        }
        inspectorWidth="w-[24rem]"
        list={
          <SectionCard className="ring-0" title="Extension manifests">
            <div className="text-muted-foreground grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.6fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.6fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
              <span>Extension</span>
              <span>Version</span>
              <span>Navigation</span>
              <span>Routes</span>
              <span>State</span>
            </div>
            {isInitialLoading ? (
              <LoadingBlock className="p-3" label="Loading extensions" />
            ) : extensions.length === 0 ? (
              <EmptyBlock
                description="Install or enable extensions to inspect contributed routes, actions, and manifests."
                title="No extensions installed"
              />
            ) : (
              <div className="divide-border divide-y">
                {extensions.map((extension) => (
                  <ExtensionRow
                    extension={extension}
                    key={extension.id}
                    onSelect={() => selectExtension(extension.id)}
                    selected={extension.id === selected?.id}
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

function ExtensionRow({
  extension,
  onSelect,
  selected,
}: {
  extension: ObservatoryExtension
  onSelect: () => void
  selected: boolean
}) {
  return (
    <button
      className={cn(
        'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1.2fr)_minmax(0,0.6fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.6fr)] items-center gap-3 px-3 py-2 text-left transition-colors',
        selected && 'bg-accent/60 hover:bg-accent/60',
      )}
      onClick={onSelect}
      type="button"
    >
      <span className="text-foreground truncate text-xs font-medium">
        {extension.name}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {extension.version ?? 'not reported'}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {contributedNav(extension).join(', ') || 'none'}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {extension.routes.map(contributedRoute).join(', ') || 'none'}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {extension.enabled ? 'enabled' : 'disabled'}
      </span>
    </button>
  )
}

function summarizeExtensions(extensions: Array<ObservatoryExtension>): {
  installed: number
  navEntries: number
  routes: number
  settingsScopes: number
} {
  return {
    installed: extensions.length,
    navEntries: extensions.reduce(
      (total, extension) => total + contributedNav(extension).length,
      0,
    ),
    routes: extensions.reduce(
      (total, extension) => total + extension.routes.length,
      0,
    ),
    settingsScopes: new Set(
      extensions
        .map((extension) => extension.settings_scope)
        .filter((scope): scope is string => Boolean(scope)),
    ).size,
  }
}

function extensionSummary(extension: ObservatoryExtension): string {
  return [
    extension.version ? `v${extension.version}` : null,
    `${contributedNav(extension).length} nav`,
    `${extension.routes.length} routes`,
    extension.settings_scope
      ? `${labelValue(extension.settings_scope)} settings`
      : null,
  ]
    .filter(Boolean)
    .join(' · ')
}

export function contributedNav(extension: ObservatoryExtension): Array<string> {
  return uniqueRoutes(extension.nav.map(contributedRoute))
}

export function contributedRoute(route: string): string {
  return route
}

function uniqueRoutes(routes: Array<string>): Array<string> {
  return Array.from(new Set(routes))
}
