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
import type { ReactNode } from 'react'

import type {
  ObservatoryExtension,
  ObservatoryExtensionDetail,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import {
  getObservatoryExtensionDetailDirect,
  getObservatoryExtensions,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'
import { labelValue, metadataDisplayText } from '@/observatory/platformMetadata'

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
    <ObservatoryPage
      kicker="Extensions"
      title="Extension registry"
      description="Installed Observatory providers, declared navigation targets, settings scopes, and manifest coverage."
      action={
        <span className="phlo-observatory-pill">
          {refreshState ? `${refreshState} · ` : ''}
          {extensions.length} installed
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell phlo-observatory-extensions-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-platform-summary">
            <PlatformMetric
              icon={<Plug className="size-4" />}
              label="Installed"
              value={summary.installed}
            />
            <PlatformMetric
              icon={<Navigation className="size-4" />}
              label="Nav entries"
              value={summary.navEntries}
            />
            <PlatformMetric
              icon={<RouteIcon className="size-4" />}
              label="Routes"
              value={summary.routes}
            />
            <PlatformMetric
              icon={<Settings className="size-4" />}
              label="Settings scopes"
              value={summary.settingsScopes}
            />
          </div>
          <div className="phlo-observatory-browser-toolbar">
            <span>
              <Plug className="size-4" />
              Extension manifests
            </span>
            <span className="phlo-observatory-pill">
              {refreshState ? `${refreshState} · ` : ''}
              {extensions.length} registered
            </span>
          </div>
          <div className="phlo-observatory-platform-table" role="table">
            <div className="phlo-observatory-platform-head" role="row">
              <span>Extension</span>
              <span>Version</span>
              <span>Navigation</span>
              <span>Routes</span>
              <span>State</span>
            </div>
            {extensions.map((extension) => (
              <ExtensionRow
                extension={extension}
                key={extension.id}
                onSelect={() => selectExtension(extension.id)}
                selected={extension.id === selected?.id}
              />
            ))}
            {isInitialLoading ? (
              <div className="phlo-observatory-run-provider-empty">
                <div>
                  <span className="phlo-observatory-inspector-label">
                    Extensions
                  </span>
                  <h2>Loading extensions</h2>
                  <p>
                    Reading installed manifests, routes, and settings scopes.
                  </p>
                </div>
              </div>
            ) : (
              extensions.length === 0 && (
                <div className="phlo-observatory-run-provider-empty">
                  <div>
                    <span className="phlo-observatory-inspector-label">
                      Extensions
                    </span>
                    <h2>No extensions installed</h2>
                    <p>
                      Install or enable extensions to inspect contributed
                      routes, actions, and manifests.
                    </p>
                  </div>
                </div>
              )
            )}
          </div>
        </div>

        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">
            Extension detail
          </div>
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <p>{extensionSummary(selected)}</p>
              <dl className="phlo-observatory-facts">
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
              </dl>
              <div className="phlo-observatory-detail-list">
                {contributedNav(selected).map((navItem) => (
                  <Link
                    className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
                    key={navItem}
                    to={navItem}
                  >
                    <span>{navItem}</span>
                    <small>navigation target</small>
                  </Link>
                ))}
                {contributedNav(selected).length === 0 && (
                  <div className="phlo-observatory-mini-row">
                    <span>Navigation</span>
                    <small>No navigation entry declared</small>
                  </div>
                )}
              </div>
              <div className="phlo-observatory-detail-list">
                {(detail.data?.routes ?? selected.routes).map((route) => (
                  <div className="phlo-observatory-mini-row" key={route}>
                    <span>{contributedRoute(route)}</span>
                    <small>route</small>
                  </div>
                ))}
                {detail.data && detail.data.capabilities.length > 0
                  ? detail.data.capabilities.map((capability) => (
                      <div
                        className="phlo-observatory-mini-row"
                        key={capability.id}
                      >
                        <span>{capability.label}</span>
                        <small>{labelValue(capability.kind)}</small>
                      </div>
                    ))
                  : null}
                {detail.data && detail.data.routes.length === 0 && (
                  <div className="phlo-observatory-mini-row">
                    <span>Routes</span>
                    <small>No extension routes declared</small>
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <h2>
                {isLoading
                  ? 'Checking extension detail'
                  : 'No extension selected'}
              </h2>
              <p>
                {isLoading
                  ? 'Reading extension manifest and contribution details.'
                  : 'Select an extension to inspect manifest and contribution details.'}
              </p>
            </>
          )}
          {detail.error && (
            <div className="phlo-observatory-panel-footer">{detail.error}</div>
          )}
          {result.error && (
            <div className="phlo-observatory-panel-footer">{result.error}</div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
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
      className="phlo-observatory-platform-row"
      data-active={selected}
      onClick={onSelect}
      role="row"
      type="button"
    >
      <span>{extension.name}</span>
      <span>{extension.version ?? 'not reported'}</span>
      <span>{contributedNav(extension).join(', ') || 'none'}</span>
      <span>{extension.routes.map(contributedRoute).join(', ') || 'none'}</span>
      <span>{extension.enabled ? 'enabled' : 'disabled'}</span>
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

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
