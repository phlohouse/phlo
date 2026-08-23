/**
 * Observatory app shell: grouped sidebar navigation over capability pages,
 * theme mode handling, recent-visit recording, and warm-up of cached
 * resources. Extension-contributed nav items merge into the core groups.
 */
import { Link, useRouterState } from '@tanstack/react-router'
import {
  Activity,
  Boxes,
  CirclePlay,
  Clipboard,
  Database,
  FileClock,
  FolderKanban,
  GitBranch,
  Import,
  LayoutDashboard,
  ListChecks,
  Logs,
  Menu,
  Monitor,
  Moon,
  Plug,
  Search,
  Server,
  Settings,
  Sun,
  UploadCloud,
  X,
} from 'lucide-react'
import { Suspense, lazy, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryCapabilities,
  ObservatoryCapabilityPage,
  ObservatoryResourceResult,
  ObservatoryTable,
} from '@/observatory/api/types'
import type { ObservatoryThemeMode } from '@/observatory/shell/theme'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  OBSERVATORY_THEME_STORAGE_KEY,
  readObservatoryThemeMode,
  resolveObservatoryTheme,
} from '@/observatory/shell/theme'
import { recordRecentVisit } from '@/observatory/shell/localActivity'
import {
  getObservatoryAssetRecords,
  getObservatoryCapabilities,
  getObservatoryDatasetRecords,
  getObservatoryGovernanceItems,
  getObservatoryLogRecords,
  getObservatoryPipelineRecords,
  getObservatoryQualityRecords,
  getObservatoryRunRecords,
  getObservatoryServices,
  getObservatoryTablePreview,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { loadCachedResource } from '@/observatory/routes/liveResource'

const ObservatoryCommandPalette = lazy(() =>
  import('@/observatory/shell/ObservatoryCommandPalette').then((module) => ({
    default: module.ObservatoryCommandPalette,
  })),
)

const fallbackPages: Array<ObservatoryCapabilityPage> = [
  corePage('overview', 'Home', '/'),
  corePage('workspace', 'Workspace', '/workspace'),
  corePage('recents', 'Recents', '/recents'),
  corePage('queries', 'Queries', '/queries'),
  corePage('query-history', 'Query History', '/query-history'),
  corePage('ingestion', 'Ingestion', '/ingestion'),
  corePage('operations', 'Recovery', '/operations'),
  corePage('tables', 'Tables', '/tables'),
  corePage('lineage', 'Lineage', '/lineage'),
  corePage('workflows', 'Workflow Builder', '/workflows/new'),
  corePage('quality', 'Quality', '/quality'),
  corePage('runs', 'Runs', '/runs'),
  corePage('branches', 'Change Review', '/branches'),
  corePage('datasets', 'Datasets', '/datasets'),
  corePage('governance', 'Governance', '/governance'),
  corePage('publishing', 'Publishing', '/publishing'),
  corePage('pipelines', 'Pipelines', '/pipelines'),
  corePage('logs', 'Logs', '/logs'),
  corePage('services', 'Services', '/services'),
  corePage('extensions', 'Extensions', '/extensions'),
  corePage('settings', 'Settings', '/settings'),
]

const navOrder = [
  'overview',
  'workspace',
  'recents',
  'datasets',
  'tables',
  'lineage',
  'queries',
  'query-history',
  'quality',
  'governance',
  'pipelines',
  'runs',
  'operations',
  'logs',
  'publishing',
  'branches',
  'workflows',
  'ingestion',
  'services',
  'storage',
  'observability',
  'apis',
  'bi',
  'extensions',
  'settings',
]

const navGroupDefinitions = [
  {
    label: 'Home',
    sections: [{ ids: ['overview', 'workspace', 'recents'] }],
  },
  {
    label: 'Data',
    sections: [
      {
        label: 'Catalog',
        ids: ['datasets', 'tables', 'lineage', 'queries', 'query-history'],
      },
      { label: 'Controls', ids: ['governance'] },
    ],
  },
  {
    label: 'Investigate',
    sections: [
      { label: 'Triage', ids: ['quality', 'operations'] },
      { label: 'Evidence', ids: ['runs', 'pipelines', 'logs'] },
    ],
  },
  {
    label: 'Deliver',
    sections: [
      { label: 'Release', ids: ['publishing', 'branches'] },
      { label: 'Automation', ids: ['ingestion', 'workflows'] },
    ],
  },
  {
    label: 'Platform',
    sections: [
      {
        label: 'Runtime',
        ids: ['services', 'storage', 'observability'],
      },
      { label: 'Interfaces', ids: ['apis', 'bi'] },
      { label: 'Configuration', ids: ['extensions', 'settings'] },
    ],
  },
] satisfies Array<{
  label: string
  sections: Array<{ label?: string; ids: Array<string> }>
}>

const warmPreviewLimit = 100
const platformTrustPageIds = new Set([
  'extensions',
  'storage',
  'observability',
  'apis',
  'bi',
])

const iconByPageId: Record<string, typeof LayoutDashboard> = {
  overview: LayoutDashboard,
  workspace: FolderKanban,
  recents: FileClock,
  queries: Database,
  'query-history': FileClock,
  ingestion: Import,
  services: Server,
  operations: Activity,
  runs: CirclePlay,
  tables: Database,
  lineage: Boxes,
  workflows: Clipboard,
  quality: ListChecks,
  logs: Logs,
  branches: GitBranch,
  extensions: Plug,
  storage: Database,
  observability: Activity,
  governance: Settings,
  datasets: Boxes,
  publishing: UploadCloud,
  pipelines: Activity,
  apis: Server,
  bi: LayoutDashboard,
  settings: Settings,
}

const navSubtitleByPageId: Record<string, string> = {
  overview: 'Health, counters, and recent activity.',
  workspace: 'Authored resources and project objects.',
  recents: 'Recently visited Observatory resources.',
  queries: 'Saved SQL and read-only query workspace.',
  'query-history': 'Browser-local query execution evidence.',
  ingestion: 'Source onboarding, freshness, and next actions.',
  services: 'Runtime services and stack status.',
  operations: 'Failed work, recovery evidence, and next actions.',
  runs: 'Orchestrator history and outcomes.',
  tables: 'Tables, previews, and query surfaces.',
  lineage: 'Lineage, dependencies, and metadata.',
  storage: 'Lakehouse storage and table providers.',
  observability: 'Signals from metrics and traces.',
  logs: 'Platform and resource events.',
  datasets: 'Governed datasets and raw candidates.',
  governance: 'Owners, classifications, controls.',
  publishing: 'Internal release readiness.',
  pipelines: 'Dataset pipeline freshness.',
  workflows: 'Create and edit workflow definitions.',
  quality: 'Checks, severity, and evidence.',
  branches: 'Branch state, reviews, and publish context.',
  apis: 'Published API surfaces.',
  bi: 'Reports, dashboards, and consumers.',
  extensions: 'Installed providers and settings.',
  settings: 'Project and Observatory preferences.',
}

const themeModes = [
  { mode: 'system', label: 'System', icon: Monitor },
  { mode: 'light', label: 'Light', icon: Sun },
  { mode: 'dark', label: 'Dark', icon: Moon },
] satisfies Array<{
  mode: ObservatoryThemeMode
  label: string
  icon: typeof Monitor
}>

export function ObservatoryShell(props: { children: ReactNode }) {
  return useObservatoryShell(props)
}

function useObservatoryShell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const [searchOpen, setSearchOpen] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [{ hydrated, systemPrefersDark, themeMode }, setThemeState] = useState({
    hydrated: false,
    systemPrefersDark: false,
    themeMode: 'system' as ObservatoryThemeMode,
  })
  const [capabilities, setCapabilities] =
    useState<ObservatoryResourceResult<ObservatoryCapabilities> | null>(null)
  const resolvedTheme = resolveObservatoryTheme(themeMode, systemPrefersDark)
  const pages = hydrated
    ? mergeFallbackPages(capabilities?.data?.pages ?? fallbackPages).map(
        normalizePlatformTrustPage,
      )
    : fallbackPages
  const navItems = pages
    .filter((page) => page.nav && page.available)
    .sort((left, right) => navRank(left.id) - navRank(right.id))
  const activePage = pageForPath(pathname, pages)
  const activePageLabel = activePage?.label
  const pagePending =
    capabilities === null &&
    activePage === null &&
    isKnownObservatoryPath(pathname)
  const pageUnavailable =
    hydrated &&
    capabilities?.data !== null &&
    activePage !== null &&
    activePage.available === false &&
    !isFallbackPage(activePage.id)

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    setThemeState({
      hydrated: true,
      systemPrefersDark: media?.matches ?? false,
      themeMode: readObservatoryThemeMode(window.localStorage),
    })
    if (!media) return

    const update = () =>
      setThemeState((current) => ({
        ...current,
        systemPrefersDark: media.matches,
      }))
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    if (!hydrated) return
    window.localStorage.setItem(OBSERVATORY_THEME_STORAGE_KEY, themeMode)
  }, [hydrated, themeMode])

  useEffect(() => {
    document.documentElement.dataset.phloObservatoryRoute = 'true'
    document.documentElement.dataset.phloObservatoryTheme = resolvedTheme
    document.documentElement.style.colorScheme = resolvedTheme

    return () => {
      delete document.documentElement.dataset.phloObservatoryRoute
      delete document.documentElement.dataset.phloObservatoryTheme
      document.documentElement.style.removeProperty('color-scheme')
    }
  }, [resolvedTheme])

  useEffect(() => {
    if (!hydrated || !activePageLabel) return
    recordRecentVisit(pathname, activePageLabel)
  }, [activePageLabel, hydrated, pathname])

  useEffect(() => {
    let cancelled = false
    async function load() {
      if (cancelled) return
      const next = await loadCachedResource(
        'observatory:capabilities',
        getObservatoryCapabilities,
        { force: true, staleMs: 30_000 },
      )
      if (!cancelled) {
        setCapabilities(next)
        warmRouteResources(next.data)
      }
    }
    void load()
    const interval = window.setInterval(load, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const isCommandSearch =
        event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey)
      if (isCommandSearch) {
        event.preventDefault()
        setSearchOpen(true)
        return
      }
      if (event.key === 'Escape' && searchOpen) {
        event.preventDefault()
        setSearchOpen(false)
        return
      }
      if (event.key === 'Escape' && mobileNavOpen) {
        event.preventDefault()
        setMobileNavOpen(false)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [mobileNavOpen, searchOpen])

  useEffect(() => setMobileNavOpen(false), [pathname])

  return (
    <main
      className="phlo-observatory"
      data-theme={resolvedTheme}
      data-theme-mode={themeMode}
      suppressHydrationWarning
    >
      <div className="phlo-observatory-nav-bar">
        <nav
          className="phlo-observatory-shell phlo-observatory-nav"
          aria-label="Observatory"
          style={{
            marginInline: 'auto',
            paddingInline: 16,
            width: 'min(1760px, calc(100vw - clamp(24px, 4vw, 72px)))',
          }}
        >
          <Link
            aria-label="Home"
            className="phlo-observatory-brand"
            title="Home"
            to="/"
          >
            <span className="phlo-observatory-mark">P</span>
            <span>Phlo Observatory</span>
          </Link>
          <div className="phlo-observatory-nav-spacer" />
          <div
            className="phlo-observatory-nav-actions"
            style={{ borderLeft: 0, paddingLeft: 0 }}
          >
            <button
              aria-expanded={mobileNavOpen}
              aria-label={
                mobileNavOpen ? 'Close navigation' : 'Open navigation'
              }
              className="phlo-observatory-mobile-nav-trigger"
              onClick={() => setMobileNavOpen((open) => !open)}
              type="button"
            >
              {mobileNavOpen ? (
                <X className="size-4" />
              ) : (
                <Menu className="size-4" />
              )}
              <span>Menu</span>
            </button>
            <button
              aria-label="Search Observatory"
              aria-expanded={searchOpen}
              aria-haspopup="dialog"
              className="phlo-observatory-nav-link phlo-observatory-search-trigger"
              onClick={() => setSearchOpen(true)}
              type="button"
            >
              <Search className="size-3.5" />
              <span>Search</span>
              <kbd>⌘K</kbd>
            </button>
            <div className="phlo-observatory-theme-toggle" aria-label="Theme">
              {themeModes.map((item) => {
                const Icon = item.icon
                return (
                  <button
                    aria-label={`${item.label} theme`}
                    aria-pressed={themeMode === item.mode}
                    data-active={themeMode === item.mode}
                    key={item.mode}
                    onClick={() =>
                      setThemeState((current) => ({
                        ...current,
                        themeMode: item.mode,
                      }))
                    }
                    suppressHydrationWarning
                    title={`${item.label} theme`}
                    type="button"
                  >
                    <Icon className="size-3.5" />
                  </button>
                )
              })}
            </div>
          </div>
        </nav>
      </div>
      <div className="phlo-observatory-shell phlo-observatory-body">
        {searchOpen && (
          <Suspense
            fallback={
              <CommandPaletteFallback onClose={() => setSearchOpen(false)} />
            }
          >
            <ObservatoryCommandPalette
              navItems={navItems}
              onClose={() => setSearchOpen(false)}
            />
          </Suspense>
        )}
        <div className="phlo-observatory-app-layout">
          <ObservatorySidebar
            hydrated={hydrated}
            items={navItems}
            mobileOpen={mobileNavOpen}
            onNavigate={() => setMobileNavOpen(false)}
            pathname={pathname}
          />
          <section className="phlo-observatory-sheet">
            {pagePending ? (
              <PendingCapabilityPage />
            ) : pageUnavailable ? (
              <UnavailablePage page={activePage} />
            ) : (
              children
            )}
          </section>
        </div>
      </div>
    </main>
  )
}

function isActive(pathname: string, href: string): boolean {
  const cleanPathname = cleanPath(pathname)
  const cleanHref = cleanPath(href)
  if (cleanHref === '/') return cleanPathname === '/'
  return (
    cleanPathname === cleanHref || cleanPathname.startsWith(`${cleanHref}/`)
  )
}

function ObservatorySidebar({
  hydrated,
  items,
  mobileOpen,
  onNavigate,
  pathname,
}: {
  hydrated: boolean
  items: Array<ObservatoryCapabilityPage>
  mobileOpen: boolean
  onNavigate: () => void
  pathname: string
}) {
  const groups = navGroups(items)
  return (
    <aside className="phlo-observatory-sidebar" data-mobile-open={mobileOpen}>
      <TooltipProvider>
        <nav aria-label="Observatory sections">
          {groups.map((group) => (
            <div className="phlo-observatory-sidebar-group" key={group.label}>
              {group.label !== 'Home' && (
                <div className="phlo-observatory-sidebar-label">
                  {group.label}
                </div>
              )}
              {group.sections.map((section) => (
                <div
                  className="phlo-observatory-sidebar-section"
                  key={section.label ?? group.label}
                >
                  {section.items.map((item) => {
                    const Icon = iconByPageId[item.id] ?? LayoutDashboard
                    const activeItem = hydrated && isActive(pathname, item.path)
                    const description =
                      navSubtitleByPageId[item.id] ??
                      'Open this Observatory surface.'
                    return (
                      <Tooltip key={item.id}>
                        <TooltipTrigger
                          render={
                            <Link
                              aria-current={activeItem ? 'page' : undefined}
                              aria-label={item.label}
                              className="phlo-observatory-sidebar-link"
                              data-active={activeItem}
                              onClick={onNavigate}
                              to={item.path}
                            />
                          }
                        >
                          <Icon className="size-4" />
                          <span>{item.label}</span>
                        </TooltipTrigger>
                        <TooltipContent className="phlo-observatory-nav-tooltip">
                          <strong>{item.label}</strong>
                          <span>{description}</span>
                        </TooltipContent>
                      </Tooltip>
                    )
                  })}
                </div>
              ))}
            </div>
          ))}
        </nav>
      </TooltipProvider>
    </aside>
  )
}

function navGroups(items: Array<ObservatoryCapabilityPage>) {
  const byId = new Map(items.map((item) => [item.id, item]))
  const used = new Set<string>()
  const groups = navGroupDefinitions
    .map((group) => {
      const sections = group.sections
        .map((section) => ({
          label: 'label' in section ? section.label : undefined,
          items: section.ids.flatMap((id) => {
            const item = byId.get(id)
            if (!item) return []
            used.add(id)
            return [item]
          }),
        }))
        .filter((section) => section.items.length > 0)
      return {
        label: group.label,
        sections,
        items: sections.flatMap((section) => section.items),
      }
    })
    .filter((group) => group.items.length > 0)
  const rest = items.filter((item) => !used.has(item.id))
  return rest.length
    ? [
        ...groups,
        {
          label: 'More',
          sections: [{ label: undefined, items: rest }],
          items: rest,
        },
      ]
    : groups
}

function CommandPaletteFallback({ onClose }: { onClose: () => void }) {
  return (
    <div
      aria-label="Command search"
      aria-modal="true"
      className="phlo-observatory-command-overlay"
      role="dialog"
    >
      <button
        aria-label="Close search"
        className="phlo-observatory-command-backdrop"
        onClick={onClose}
        type="button"
      />
      <div className="phlo-observatory-search-popover">
        <div className="phlo-observatory-command phlo-observatory-command-palette">
          <div className="phlo-observatory-search-field">
            <Search className="size-4" />
            <span>Loading search…</span>
            <kbd>Esc</kbd>
          </div>
        </div>
      </div>
    </div>
  )
}

function navRank(pageId: string): number {
  const index = navOrder.indexOf(pageId)
  return index === -1 ? navOrder.length : index
}

function mergeFallbackPages(
  pages: Array<ObservatoryCapabilityPage>,
): Array<ObservatoryCapabilityPage> {
  const merged = new Map<string, ObservatoryCapabilityPage>()
  for (const page of pages) {
    const fallback = fallbackPages.find((item) => item.id === page.id)
    merged.set(page.id, fallback ? { ...page, nav: fallback.nav } : page)
  }
  for (const fallback of fallbackPages) {
    const page = merged.get(fallback.id)
    if (!page) merged.set(fallback.id, fallback)
  }
  return Array.from(merged.values())
}

function isFallbackPage(pageId: string): boolean {
  return fallbackPages.some((page) => page.id === pageId)
}

function normalizePlatformTrustPage(
  page: ObservatoryCapabilityPage,
): ObservatoryCapabilityPage {
  if (!platformTrustPageIds.has(page.id)) return page
  return {
    ...page,
    available: true,
    nav: true,
  }
}

function pageForPath(
  pathname: string,
  pages: Array<ObservatoryCapabilityPage>,
): ObservatoryCapabilityPage | null {
  const cleanPathname = cleanPath(pathname)
  const exact = pages.find((page) => cleanPathname === cleanPath(page.path))
  if (exact) return exact

  const canonicalChildRouteParents: Record<string, string> = {
    '/datasets/': 'datasets',
    '/extensions/': 'extensions',
  }
  const match = Object.entries(canonicalChildRouteParents).find(([prefix]) =>
    cleanPathname.startsWith(prefix),
  )
  if (!match) return null
  return pages.find((page) => page.id === match[1]) ?? null
}

function isKnownObservatoryPath(pathname: string): boolean {
  const cleanPathname = cleanPath(pathname)
  if (cleanPathname === '/') return true
  return [
    '/apis',
    '/bi',
    '/branches',
    '/datasets',
    '/extensions',
    '/governance',
    '/lineage',
    '/logs',
    '/observability',
    '/operations',
    '/pipelines',
    '/publishing',
    '/quality',
    '/runs',
    '/services',
    '/settings',
    '/storage',
    '/tables',
    '/workflows',
  ].some(
    (path) => cleanPathname === path || cleanPathname.startsWith(`${path}/`),
  )
}

function cleanPath(path: string): string {
  return path
}

function warmRouteResources(capabilities: ObservatoryCapabilities | null) {
  if (!capabilities) return
  const features = capabilities.features
  void loadCachedResource('observatory:services', getObservatoryServices, {
    staleMs: 120_000,
  })
  if (features.tables) {
    void loadCachedResource('observatory:tables', getObservatoryTableRecords, {
      staleMs: 120_000,
    }).then((result) => warmDefaultTablePreview(result.data ?? []))
  }
  if (features.lineage) {
    void loadCachedResource('observatory:assets', getObservatoryAssetRecords, {
      staleMs: 120_000,
    })
  }
  if (features.runs) {
    void loadCachedResource('observatory:runs', getObservatoryRunRecords, {
      staleMs: 120_000,
    })
  }
  if (features.quality) {
    void loadCachedResource(
      'observatory:quality',
      getObservatoryQualityRecords,
      {
        staleMs: 120_000,
      },
    )
  }
  if (features.logs) {
    void loadCachedResource('observatory:logs', getObservatoryLogRecords, {
      staleMs: 120_000,
    })
  }
  if (features.datasets || features.publishing) {
    void loadCachedResource(
      'observatory:datasets',
      getObservatoryDatasetRecords,
      {
        staleMs: 120_000,
      },
    )
  }
  if (features.governance) {
    void loadCachedResource(
      'observatory:governance-matrix',
      getObservatoryGovernanceItems,
      {
        staleMs: 120_000,
      },
    )
  }
  if (features.pipelines) {
    void loadCachedResource(
      'observatory:pipelines',
      getObservatoryPipelineRecords,
      {
        staleMs: 120_000,
      },
    )
  }
}

function warmDefaultTablePreview(tables: Array<ObservatoryTable>) {
  const table = choosePreviewTable(tables)
  if (!table) return
  void loadCachedResource(
    `observatory:table-preview:${table.id}:${warmPreviewLimit}:0:0`,
    () =>
      getObservatoryTablePreview({
        data: { tableId: table.id, limit: warmPreviewLimit, offset: 0 },
      }),
    { staleMs: 120_000 },
  )
}

function choosePreviewTable(
  tables: Array<ObservatoryTable>,
): ObservatoryTable | null {
  return (
    tables.find((table) => isQueryableTable(table) && hasRowCount(table)) ??
    tables.find(
      (table) => isQueryableTable(table) && tableLane(table) === 'silver',
    ) ??
    tables.find(isQueryableTable) ??
    tables.find((table) => tableLane(table) === 'silver') ??
    tables[0] ??
    null
  )
}

function isQueryableTable(table: ObservatoryTable): boolean {
  const state = table.metadata.catalog_state
  if (state === 'queryable') return true
  if (state === 'model_only') return false
  return table.metadata.catalog_present === true
}

function hasRowCount(table: ObservatoryTable): boolean {
  return (
    table.metadata.rows !== undefined ||
    table.metadata.records !== undefined ||
    table.metadata.row_count !== undefined
  )
}

function tableLane(table: ObservatoryTable): string {
  return String(table.namespace ?? table.schema_name ?? '').toLowerCase()
}

function PendingCapabilityPage() {
  return (
    <div className="phlo-observatory-content">
      <header className="phlo-observatory-section-header">
        <div>
          <div className="phlo-observatory-kicker">Checking capability</div>
          <h1 className="phlo-observatory-title">Loading surface</h1>
          <p className="phlo-observatory-subtitle">
            Observatory is checking the project packages before opening this
            page.
          </p>
        </div>
      </header>
      <section className="phlo-observatory-panel phlo-observatory-empty-panel">
        <h2>Reading project capabilities</h2>
        <p>Pages appear here only when the matching provider is installed.</p>
      </section>
    </div>
  )
}

function UnavailablePage({ page }: { page: ObservatoryCapabilityPage }) {
  const providers = page.providers.length ? page.providers.join(', ') : 'none'

  return (
    <div className="phlo-observatory-content">
      <header className="phlo-observatory-section-header">
        <div>
          <div className="phlo-observatory-kicker">
            Capability not connected
          </div>
          <h1 className="phlo-observatory-title">{page.label}</h1>
          <p className="phlo-observatory-subtitle">
            {page.reason ??
              'This surface appears when a project package contributes data for it.'}
          </p>
        </div>
      </header>
      <section className="phlo-observatory-panel phlo-observatory-empty-panel phlo-observatory-capability-panel">
        <div>
          <h2>{page.label} is not available in this stack</h2>
          <p>
            Keep working in the connected Observatory areas, or add the package
            that provides this read model when the project needs it.
          </p>
        </div>
        <dl className="phlo-observatory-capability-grid">
          <dt>Status</dt>
          <dd>not connected</dd>
          <dt>Providers</dt>
          <dd>{providers}</dd>
          <dt>Next step</dt>
          <dd>enable matching package</dd>
        </dl>
      </section>
    </div>
  )
}

function corePage(
  id: string,
  label: string,
  path: string,
): ObservatoryCapabilityPage {
  return {
    id,
    label,
    path,
    available: true,
    nav: true,
    providers: [],
    metadata: {},
  }
}
