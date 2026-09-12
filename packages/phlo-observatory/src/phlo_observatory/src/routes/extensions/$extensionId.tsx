/**
 * Extension detail route. Fetches one extension's detail directly, without
 * the shared cache, and lists its contributed routes and nav entries.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Navigation, Plug, Route as RouteIcon, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'

import type {
  ObservatoryExtensionDetail,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import { getObservatoryExtensionDetailDirect } from '@/observatory/api/resources'
import {
  labelValue,
  metadataDisplayText,
  platformMetadataRows,
} from '@/observatory/platformMetadata'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { EmptyBlock } from '@/components/observatory/states'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { SectionCard } from '@/components/observatory/section'
import { StatusBadge } from '@/components/observatory/status'
import { Badge } from '@/components/ui/badge'

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
    <Page>
      <PageHeader
        actions={<Badge variant="secondary">{statusLabel}</Badge>}
        breadcrumb={[
          { label: 'Extensions', to: '/extensions' },
          { label: extension?.name ?? extensionId },
        ]}
        description="Extension manifest, declared navigation targets, routes, settings scope, and declared capabilities."
        title={extension?.name ?? extensionId}
      />
      {detail && extension ? (
        <>
          <StatGrid>
            <StatCard
              icon={<Plug className="size-3.5" />}
              label="State"
              state={extension.enabled ? 'ok' : 'unknown'}
              value={extension.enabled ? 'on' : 'off'}
            />
            <StatCard
              icon={<Navigation className="size-3.5" />}
              label="Nav entries"
              value={nav.length}
            />
            <StatCard
              icon={<RouteIcon className="size-3.5" />}
              label="Routes"
              value={routes.length}
            />
            <StatCard
              icon={<Settings className="size-3.5" />}
              label="Settings"
              value={
                extension.settings_scope
                  ? labelValue(extension.settings_scope)
                  : 'none'
              }
            />
          </StatGrid>
          <SplitView
            inspector={
              <>
                <InspectorSection label="Manifest">
                  <div className="flex items-center gap-2">
                    <span className="text-foreground text-xs font-semibold">
                      {extension.name}
                    </span>
                    <StatusBadge
                      label={extension.enabled ? 'enabled' : 'disabled'}
                      state={extension.enabled ? 'ok' : 'unknown'}
                    />
                  </div>
                  <p className="text-muted-foreground text-xs">
                    {extension.version
                      ? `v${extension.version}`
                      : 'No version declared.'}
                  </p>
                  <FactGrid>
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
                  </FactGrid>
                </InspectorSection>
                <InspectorSection label="Links">
                  <div className="divide-border divide-y border-y">
                    <Link
                      className="hover:bg-accent/50 flex items-center justify-between gap-2 py-1.5 transition-colors"
                      to="/extensions"
                    >
                      <span className="text-foreground text-[11px]">
                        Extension registry
                      </span>
                      <span className="text-muted-foreground font-mono text-[10px]">
                        back to all manifests
                      </span>
                    </Link>
                    {nav.map((navItem) => (
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
                  </div>
                </InspectorSection>
                <InspectorSection label="Metadata">
                  {platformMetadataRows(extension.metadata).length ? (
                    <FactGrid>
                      {platformMetadataRows(extension.metadata).map((row) => (
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
                </InspectorSection>
              </>
            }
            inspectorWidth="w-[24rem]"
            list={
              <SectionCard className="ring-0" title="Manifest entries">
                <div className="text-muted-foreground grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.7fr)_minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,0.6fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
                  <span>Entry</span>
                  <span>Kind</span>
                  <span>Target</span>
                  <span>Source</span>
                  <span>State</span>
                </div>
                {nav.length === 0 &&
                routes.length === 0 &&
                detail.capabilities.length === 0 ? (
                  <EmptyBlock
                    description="This extension is installed but did not declare routes, navigation, or capabilities."
                    title="No manifest entries declared"
                  />
                ) : (
                  <div className="divide-border divide-y">
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
                  </div>
                )}
              </SectionCard>
            }
          />
        </>
      ) : (
        <SectionCard>
          <EmptyBlock
            description={result.error ?? undefined}
            title={
              result.error
                ? 'Extension detail unavailable'
                : 'Checking extension detail'
            }
          />
        </SectionCard>
      )}
    </Page>
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
    <div
      className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.7fr)_minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,0.6fr)] items-center gap-3 px-3 py-2"
      role="row"
    >
      <span className="text-foreground truncate text-xs">{entry}</span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {kind}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {target}
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        manifest
      </span>
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {state}
      </span>
    </div>
  )
}

function contributedRoute(route: string): string {
  return route
}
