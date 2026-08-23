/**
 * Extension detail route. Fetches one extension's detail directly, without
 * the shared cache, and lists its contributed routes and nav entries.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Navigation, Plug, Route as RouteIcon, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryExtensionDetail,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import { getObservatoryExtensionDetailDirect } from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  labelValue,
  metadataDisplayText,
  platformMetadataRows,
} from '@/observatory/platformMetadata'

export const Route = createFileRoute('/extensions/$extensionId')({
  component: ExtensionDetailRoute,
})

function ExtensionDetailRoute() {
  const { extensionId } = Route.useParams()
  return <ExtensionDetailView extensionId={extensionId} />
}

export function ExtensionDetailView({ extensionId }: { extensionId: string }) {
  const [result, setResult] = useState<
    ObservatoryResourceResult<ObservatoryExtensionDetail>
  >({
    data: null,
    error: null,
  })

  useEffect(() => {
    let cancelled = false
    void getObservatoryExtensionDetailDirect({ extensionId })
      .then((next) => {
        if (!cancelled) setResult(next)
      })
      .catch(() => {
        if (!cancelled) {
          setResult({
            data: null,
            error: 'Extension detail is unavailable.',
          })
        }
      })
    return () => {
      cancelled = true
    }
  }, [extensionId])

  const detail = result.data
  const extension = detail?.extension
  const routes = detail?.routes.map(contributedRoute) ?? []
  const nav = detail?.nav.map(contributedRoute) ?? []
  const statusLabel = result.error
    ? 'unavailable'
    : extension
      ? extension.enabled
        ? 'enabled'
        : 'disabled'
      : 'checking'

  return (
    <ObservatoryPage
      action={<span className="phlo-observatory-pill">{statusLabel}</span>}
      description="Extension manifest, declared navigation targets, routes, settings scope, and declared capabilities."
      kicker="Extension"
      title={extension?.name ?? extensionId}
    >
      {detail && extension ? (
        <section className="phlo-observatory-command phlo-observatory-surface-shell phlo-observatory-extensions-shell">
          <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
            <div className="phlo-observatory-platform-summary">
              <Metric
                icon={<Plug className="size-4" />}
                label="State"
                value={extension.enabled ? 'on' : 'off'}
              />
              <Metric
                icon={<Navigation className="size-4" />}
                label="Nav entries"
                value={nav.length}
              />
              <Metric
                icon={<RouteIcon className="size-4" />}
                label="Routes"
                value={routes.length}
              />
              <Metric
                icon={<Settings className="size-4" />}
                label="Settings"
                value={
                  extension.settings_scope
                    ? labelValue(extension.settings_scope)
                    : 'none'
                }
              />
            </div>
            <div className="phlo-observatory-browser-toolbar">
              <span>
                <RouteIcon className="size-4" />
                Manifest entries
              </span>
              <span className="phlo-observatory-pill">
                {nav.length + routes.length} entries
              </span>
            </div>
            <div className="phlo-observatory-platform-table" role="table">
              <div className="phlo-observatory-platform-head" role="row">
                <span>Entry</span>
                <span>Kind</span>
                <span>Target</span>
                <span>Source</span>
                <span>State</span>
              </div>
              {nav.map((navItem) => (
                <ManifestRow
                  entry={navItem}
                  key={`nav:${navItem}`}
                  kind="navigation"
                  state="active"
                  target={navItem}
                />
              ))}
              {routes.map((route) => (
                <ManifestRow
                  entry={route}
                  key={`route:${route}`}
                  kind="route"
                  state="registered"
                  target={route}
                />
              ))}
              {detail.capabilities.map((capability) => (
                <ManifestRow
                  entry={capability.label}
                  key={`capability:${capability.id}`}
                  kind={labelValue(capability.kind)}
                  state="declared"
                  target={capability.id}
                />
              ))}
              {nav.length === 0 &&
                routes.length === 0 &&
                detail.capabilities.length === 0 && (
                  <div className="phlo-observatory-run-provider-empty">
                    <div>
                      <span className="phlo-observatory-inspector-label">
                        Manifest
                      </span>
                      <h2>No manifest entries declared</h2>
                      <p>
                        This extension is installed but did not declare routes,
                        navigation, or capabilities.
                      </p>
                    </div>
                  </div>
                )}
            </div>
          </div>

          <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
            <div className="phlo-observatory-inspector-label">Manifest</div>
            <h2>{extension.name}</h2>
            <p>
              {extension.version
                ? `v${extension.version}`
                : 'No version declared.'}
            </p>
            <dl className="phlo-observatory-facts">
              <Fact
                label="State"
                value={extension.enabled ? 'enabled' : 'disabled'}
              />
              <Fact
                label="Version"
                value={extension.version ?? 'not reported'}
              />
              <Fact
                label="Settings"
                value={
                  extension.settings_scope
                    ? labelValue(extension.settings_scope)
                    : 'none'
                }
              />
              <Fact
                label="Plugin"
                value={metadataDisplayText(extension, 'plugin')}
              />
            </dl>
            <div className="phlo-observatory-detail-list">
              <Link
                className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
                to="/extensions"
              >
                <span>Extension registry</span>
                <small>back to all manifests</small>
              </Link>
              {nav.map((navItem) => (
                <Link
                  className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
                  key={navItem}
                  to={navItem}
                >
                  <span>{navItem}</span>
                  <small>navigation target</small>
                </Link>
              ))}
            </div>
            <div className="phlo-observatory-detail-list">
              {platformMetadataRows(extension.metadata).map((row) => (
                <div className="phlo-observatory-mini-row" key={row.label}>
                  <span>{row.label}</span>
                  <small>{row.value}</small>
                </div>
              ))}
            </div>
          </aside>
        </section>
      ) : (
        <div className="phlo-observatory-empty-state">
          {result.error ?? 'Checking extension detail'}
        </div>
      )}
    </ObservatoryPage>
  )
}

function ManifestRow({
  entry,
  kind,
  state,
  target,
}: {
  entry: string
  kind: string
  state: string
  target: string
}) {
  return (
    <div className="phlo-observatory-platform-row" role="row">
      <span>{entry}</span>
      <span>{kind}</span>
      <span>{target}</span>
      <span>manifest</span>
      <span>{state}</span>
    </div>
  )
}

function Metric({
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

function contributedRoute(route: string): string {
  return route
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}
