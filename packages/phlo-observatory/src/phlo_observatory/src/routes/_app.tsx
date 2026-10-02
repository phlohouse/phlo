/** Defines the authenticated application shell and loads its shared status data. */
import { Outlet, createFileRoute, useRouterState } from '@tanstack/react-router'
import { getOverview } from '@/lib/data/api/core'
import { environmentSearchSchema } from '@/lib/data/api/client'
import { Sidebar } from '@/components/phlo/sidebar'
import { MobileTabBar, MobileTopBar } from '@/components/phlo/mobile-nav'
import { CommandPaletteProvider } from '@/components/phlo/command-palette'
import { PageSkeleton, RouteError } from '@/components/phlo/states'

/**
 * App shell. Desktop: 248px sidebar + rounded main panel. Phones (< lg): top bar,
 * full-width panel and a bottom tab bar. Every page renders inside the panel.
 */
export const Route = createFileRoute('/_app')({
  validateSearch: environmentSearchSchema,
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getOverview({ data: deps.env }),
  pendingComponent: PageSkeleton,
  errorComponent: RouteError,
  component: AppLayout,
})

function AppLayout() {
  const { overview, services, me } = Route.useLoaderData()
  const apiUnavailable = useRouterState({
    select: (s) => s.matches.some((match) => match.status === 'error'),
  })
  const env = overview.env
  const openIncidentCount = apiUnavailable
    ? null
    : (overview.incident_counts.open ?? 0)

  return (
    <CommandPaletteProvider env={env}>
      <div className="flex h-dvh overflow-hidden bg-background">
        <div className="hidden lg:flex">
          <Sidebar
            env={env}
            openIncidentCount={openIncidentCount}
            services={apiUnavailable ? [] : services}
            identity={me}
          />
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileTopBar env={env} />
          <main className="flex min-h-0 flex-1 flex-col overflow-hidden bg-card lg:my-2 lg:mr-2 lg:rounded-xl lg:border lg:border-border-card">
            {env === 'staging' ? <div className="env-stripe" /> : null}
            <Outlet />
          </main>
          <MobileTabBar env={env} openIncidents={openIncidentCount} />
        </div>
      </div>
    </CommandPaletteProvider>
  )
}
