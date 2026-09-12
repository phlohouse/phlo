/**
 * Observatory app shell: a fixed mission sidebar with grouped navigation,
 * a compact top bar (page context, command search, theme), and the routed
 * content region. Capability pages from phlo-api gate which nav entries
 * render; extension-contributed pages append as their own group.
 */
import { Link, useRouterState } from '@tanstack/react-router'
import { Menu, Monitor, Moon, Search, Sun } from 'lucide-react'
import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryCapabilities,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import type { ObservatoryThemeMode } from '@/observatory/shell/theme'
import type { NavItem } from '@/components/shell/nav'
import {
  getObservatoryCapabilities,
  getObservatoryOverview,
} from '@/observatory/api/resources'
import { recordRecentVisit } from '@/observatory/shell/localActivity'
import {
  OBSERVATORY_THEME_STORAGE_KEY,
  readObservatoryThemeMode,
  resolveObservatoryTheme,
} from '@/observatory/shell/theme'
import { loadCachedResource } from '@/observatory/routes/liveResource'
import { useObservatoryExtensions } from '@/extensions/registry'
import { NAV_GROUPS, navItemForPath } from '@/components/shell/nav'
import { HealthDot } from '@/components/observatory/status'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent } from '@/components/ui/sheet'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'

const CommandPalette = lazy(() =>
  import('@/components/shell/command-palette').then((module) => ({
    default: module.CommandPalette,
  })),
)

const themeModes = [
  { mode: 'light', label: 'Light', icon: Sun },
  { mode: 'dark', label: 'Dark', icon: Moon },
  { mode: 'system', label: 'System', icon: Monitor },
] satisfies Array<{
  mode: ObservatoryThemeMode
  label: string
  icon: typeof Monitor
}>

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const [searchOpen, setSearchOpen] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [hydrated, setHydrated] = useState(false)
  const [systemPrefersDark, setSystemPrefersDark] = useState(false)
  const [themeMode, setThemeMode] = useState<ObservatoryThemeMode>('system')
  const [capabilities, setCapabilities] =
    useState<ObservatoryResourceResult<ObservatoryCapabilities> | null>(null)
  const [health, setHealth] = useState<{
    state: string
    message?: string | null
  } | null>(null)
  const { navItems: extensionNavItems } = useObservatoryExtensions()

  const resolvedTheme = resolveObservatoryTheme(themeMode, systemPrefersDark)

  // Capability pages gate nav visibility; unknown page ids stay visible so a
  // partially deployed API never blanks the console.
  const capabilityById = useMemo(() => {
    const map = new Map<
      string,
      { nav: boolean; available: boolean; reason?: string | null }
    >()
    for (const page of capabilities?.data?.pages ?? []) {
      map.set(page.id, page)
    }
    return map
  }, [capabilities])

  const groups = useMemo(() => {
    const core = NAV_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        const cap = capabilityById.get(item.id)
        return !cap || cap.nav
      }),
    })).filter((group) => group.items.length > 0)
    const extra: Array<NavItem> = extensionNavItems.map((item) => ({
      id: `extension:${item.title}:${item.to}`,
      label: item.title,
      path: item.to,
      description: 'Extension page',
    }))
    if (extra.length) {
      core.push({
        id: 'extension-pages',
        label: 'Extensions',
        items: extra,
      })
    }
    return core
  }, [capabilityById, extensionNavItems])

  const activeItem = navItemForPath(pathname)
  const activeCap = activeItem ? capabilityById.get(activeItem.id) : null
  const pageUnavailable =
    hydrated && activeCap !== null && activeCap?.available === false

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    setSystemPrefersDark(media?.matches ?? false)
    setThemeMode(readObservatoryThemeMode(window.localStorage))
    setHydrated(true)
    if (!media) return
    const update = () => setSystemPrefersDark(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    if (!hydrated) return
    window.localStorage.setItem(OBSERVATORY_THEME_STORAGE_KEY, themeMode)
  }, [hydrated, themeMode])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolvedTheme === 'dark')
    document.documentElement.style.colorScheme = resolvedTheme
    return () => {
      document.documentElement.classList.remove('dark')
      document.documentElement.style.removeProperty('color-scheme')
    }
  }, [resolvedTheme])

  useEffect(() => {
    if (!hydrated || !activeItem) return
    recordRecentVisit(pathname, activeItem.label)
  }, [activeItem, hydrated, pathname])

  useEffect(() => {
    let cancelled = false
    async function load() {
      const [nextCapabilities, nextOverview] = await Promise.all([
        loadCachedResource(
          'observatory:capabilities',
          getObservatoryCapabilities,
          {
            force: true,
            staleMs: 30_000,
          },
        ),
        loadCachedResource('observatory:overview', getObservatoryOverview, {
          force: true,
          staleMs: 30_000,
        }),
      ])
      if (!cancelled) {
        setCapabilities(nextCapabilities)
        setHealth(nextOverview.data?.health ?? null)
      }
    }
    void load()
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') void load()
    }, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        setSearchOpen((open) => !open)
      }
      if (event.key === 'Escape') {
        setSearchOpen(false)
        setMobileNavOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  useEffect(() => setMobileNavOpen(false), [pathname])

  const sidebar = (
    <SidebarNav
      groups={groups}
      hydrated={hydrated}
      onNavigate={() => setMobileNavOpen(false)}
      pathname={pathname}
    />
  )

  return (
    <div className="bg-background text-foreground flex h-svh overflow-hidden">
      {/* Desktop sidebar */}
      <aside className="bg-sidebar border-sidebar-border hidden w-56 flex-none flex-col border-r md:flex">
        {sidebar}
      </aside>

      {/* Mobile sidebar */}
      <Sheet onOpenChange={setMobileNavOpen} open={mobileNavOpen}>
        <SheetContent className="bg-sidebar w-64 border-r p-0" side="left">
          {sidebar}
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="border-border bg-background/95 supports-backdrop-filter:bg-background/80 flex h-11 flex-none items-center gap-2 border-b px-3 backdrop-blur">
          <Button
            aria-label="Open navigation"
            className="md:hidden"
            onClick={() => setMobileNavOpen(true)}
            size="icon-sm"
            variant="ghost"
          >
            <Menu className="size-4" />
          </Button>
          <div className="text-muted-foreground flex min-w-0 items-center gap-1.5 text-xs">
            <span className="hidden sm:inline">Observatory</span>
            <span className="hidden sm:inline">/</span>
            <span className="text-foreground font-medium">
              {activeItem?.label ?? 'Page'}
            </span>
          </div>
          <div className="flex-1" />
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger
                render={<span className="flex items-center gap-1.5" />}
              >
                <HealthDot state={health?.state ?? 'unknown'} />
                <span className="text-muted-foreground hidden font-mono text-[10px] tracking-wide uppercase lg:inline">
                  {health?.state ?? 'link'}
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {health?.message ?? 'Waiting for lakehouse status'}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <button
            aria-expanded={searchOpen}
            aria-haspopup="dialog"
            className="border-input bg-surface-sunken text-muted-foreground hover:border-ring/60 hover:text-foreground flex h-7 w-44 items-center gap-2 rounded-md border px-2 text-xs transition-colors sm:w-56"
            onClick={() => setSearchOpen(true)}
            type="button"
          >
            <Search className="size-3.5" />
            <span className="flex-1 text-left">Search</span>
            <kbd className="border-border text-muted-foreground hidden rounded-sm border px-1 font-mono text-[9px] sm:inline">
              ⌘K
            </kbd>
          </button>
          <div className="border-border flex items-center rounded-md border p-0.5">
            {themeModes.map((item) => {
              const Icon = item.icon
              const active = themeMode === item.mode
              return (
                <button
                  aria-label={`${item.label} theme`}
                  aria-pressed={active}
                  className={cn(
                    'flex size-6 items-center justify-center rounded-sm transition-colors',
                    active
                      ? 'bg-accent text-foreground'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                  key={item.mode}
                  onClick={() => setThemeMode(item.mode)}
                  suppressHydrationWarning
                  title={`${item.label} theme`}
                  type="button"
                >
                  <Icon className="size-3" />
                </button>
              )
            })}
          </div>
        </header>

        {/* Content */}
        <main className="min-h-0 flex-1 overflow-y-auto">
          {pageUnavailable ? (
            <UnavailablePage label={activeItem?.label ?? 'This page'} />
          ) : (
            children
          )}
        </main>
      </div>

      {searchOpen && (
        <Suspense fallback={null}>
          <CommandPalette onClose={() => setSearchOpen(false)} />
        </Suspense>
      )}
    </div>
  )
}

function SidebarNav({
  groups,
  hydrated,
  onNavigate,
  pathname,
}: {
  groups: Array<{ id: string; label: string; items: Array<NavItem> }>
  hydrated: boolean
  onNavigate: () => void
  pathname: string
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <Link
        aria-label="Observatory home"
        className="border-sidebar-border flex h-11 flex-none items-center gap-2.5 border-b px-3"
        to="/"
      >
        <span className="bg-primary text-primary-foreground flex size-6 items-center justify-center rounded-sm font-mono text-[11px] font-bold">
          Φ
        </span>
        <span className="flex flex-col leading-none">
          <span className="text-sidebar-foreground text-xs font-semibold tracking-wide">
            PHLO
          </span>
          <span className="text-muted-foreground font-mono text-[9px] tracking-widest uppercase">
            Observatory
          </span>
        </span>
      </Link>
      <ScrollArea className="min-h-0 flex-1">
        <nav aria-label="Observatory" className="flex flex-col gap-4 p-2">
          {groups.map((group) => (
            <div key={group.id}>
              <div className="text-muted-foreground px-2 pt-1 pb-1 text-[10px] font-medium tracking-widest uppercase">
                {group.label}
              </div>
              <div className="flex flex-col gap-px">
                {group.items.map((item) => {
                  const Icon = item.icon
                  const active =
                    hydrated &&
                    (item.path === '/'
                      ? pathname === '/'
                      : pathname.startsWith(item.path))
                  return (
                    <TooltipProvider key={item.id}>
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Link
                              aria-current={active ? 'page' : undefined}
                              className={cn(
                                'relative flex h-7 items-center gap-2 rounded-md px-2 text-xs transition-colors',
                                active
                                  ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                                  : 'text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground',
                              )}
                              onClick={onNavigate}
                              to={item.path}
                            />
                          }
                        >
                          {Icon && (
                            <Icon
                              className={cn(
                                'size-3.5 flex-none',
                                active
                                  ? 'text-primary'
                                  : 'text-muted-foreground',
                              )}
                            />
                          )}
                          <span className="truncate">{item.label}</span>
                          {active && (
                            <span className="bg-primary absolute top-1/2 -left-2 h-3.5 w-0.5 -translate-y-1/2 rounded-full" />
                          )}
                        </TooltipTrigger>
                        <TooltipContent side="right">
                          {item.description}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>
      </ScrollArea>
      <Separator />
      <div className="text-muted-foreground flex h-9 flex-none items-center gap-2 px-3">
        <span className="font-mono text-[9px] tracking-widest uppercase">
          Lakehouse mission control
        </span>
      </div>
    </div>
  )
}

function UnavailablePage({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="max-w-sm text-center">
        <h1 className="text-foreground text-sm font-semibold">
          {label} is not available
        </h1>
        <p className="text-muted-foreground mt-1 text-xs">
          This surface is disabled by the current lakehouse configuration.
          Enable the capability that provides it to bring it online.
        </p>
      </div>
    </div>
  )
}
