/**
 * Observatory app shell: the sheet. A continuous-form printout framed by
 * sprocket-hole margins, headed by a report masthead (title stamp, sheet
 * number, health stamp, search field) and a two-level contents index —
 * numbered mission sections with their pages printed beneath the active
 * section. No sidebar; the sheet is a single column of bands and rules.
 */
import { Link, useRouterState } from '@tanstack/react-router'
import { Search } from 'lucide-react'
import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryCapabilities,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import type { NavItem } from '@/components/shell/nav'
import {
  getObservatoryCapabilities,
  getObservatoryOverview,
} from '@/observatory/api/resources'
import { recordRecentVisit } from '@/observatory/shell/localActivity'
import { loadCachedResource } from '@/observatory/routes/liveResource'
import { useObservatoryExtensions } from '@/extensions/registry'
import { NAV_GROUPS, navItemForPath } from '@/components/shell/nav'
import { HealthDot } from '@/components/observatory/status'
import { cn } from '@/lib/utils'
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

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const [searchOpen, setSearchOpen] = useState(false)
  const [hydrated, setHydrated] = useState(false)
  const [capabilities, setCapabilities] =
    useState<ObservatoryResourceResult<ObservatoryCapabilities> | null>(null)
  const [health, setHealth] = useState<{
    state: string
    message?: string | null
  } | null>(null)
  const { navItems: extensionNavItems } = useObservatoryExtensions()

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
      core.push({ id: 'extension-pages', label: 'Extensions', items: extra })
    }
    return core
  }, [capabilityById, extensionNavItems])

  const activeItem = navItemForPath(pathname)
  const activeGroup = groups.find((group) =>
    group.items.some((item) => item.id === activeItem?.id),
  )
  const activeCap = activeItem ? capabilityById.get(activeItem.id) : null
  const pageUnavailable =
    hydrated && activeCap !== null && activeCap?.available === false
  const groupIndex = activeGroup ? groups.indexOf(activeGroup) : -1

  useEffect(() => setHydrated(true), [])

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
          { force: true, staleMs: 30_000 },
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
      if (event.key === 'Escape') setSearchOpen(false)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  return (
    <div className="bg-paper text-ink flex h-svh overflow-hidden">
      {/* Left sprocket rail with the active section stamped vertically. */}
      <aside
        aria-hidden="true"
        className="sprocket border-rule-soft bg-band/60 hidden w-9 flex-none flex-col items-center border-r pt-3 sm:flex"
      >
        {activeGroup && (
          <span className="text-ink-soft font-mono text-[9px] font-bold tracking-[0.2em] uppercase [writing-mode:vertical-rl]">
            SEC.{String(groupIndex + 1).padStart(2, '0')} {activeGroup.label}
          </span>
        )}
      </aside>

      {/* The sheet */}
      <div className="bg-sheet flex min-w-0 flex-1 flex-col">
        <header className="border-rule-soft flex-none border-b">
          {/* Report masthead */}
          <div className="rule-double flex h-11 items-center gap-3 px-4">
            <Link
              aria-label="Observatory home"
              className="flex items-baseline gap-2"
              to="/"
            >
              <span className="stamp text-sm">Phlo Observatory</span>
              <span className="text-ink-faint hidden font-mono text-[10px] tracking-widest uppercase md:inline">
                Lakehouse status report
              </span>
            </Link>
            <span className="text-ink-faint hidden font-mono text-[10px] tracking-widest uppercase lg:inline">
              FORM OBS-{String(Math.max(groupIndex, 0) + 1).padStart(2, '0')}
            </span>
            <div className="flex-1" />
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger
                  render={
                    <span className="border-rule-soft flex items-center gap-1.5 border px-1.5 py-0.5" />
                  }
                >
                  <HealthDot state={health?.state ?? 'unknown'} />
                  <span className="font-mono text-[9px] font-bold tracking-[0.16em] uppercase">
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
              className="border-rule-soft hover:border-ink text-ink-soft hover:text-ink flex h-6 items-center gap-1.5 border px-2 font-mono text-[10px] tracking-wider uppercase"
              onClick={() => setSearchOpen(true)}
              type="button"
            >
              <Search className="size-3" />
              <span className="hidden sm:inline">Search</span>
              <kbd className="border-rule-soft hidden border px-0.5 font-mono text-[9px] sm:inline">
                ⌘K
              </kbd>
            </button>
          </div>

          {/* Contents index: numbered sections, then the active section's pages. */}
          <nav aria-label="Observatory" className="px-4">
            <div className="scrollbar-thin flex items-center gap-0 overflow-x-auto font-mono text-[10px] font-bold tracking-[0.14em] uppercase">
              <span className="text-ink-faint mr-3 flex-none py-1.5">
                Contents
              </span>
              {groups.map((group, index) => {
                const active = group.id === activeGroup?.id
                const first = group.items[0]
                return (
                  <span className="flex flex-none items-center" key={group.id}>
                    {index > 0 && (
                      <span className="text-rule-soft mx-1.5">|</span>
                    )}
                    {first && (
                      <Link
                        aria-current={active ? 'true' : undefined}
                        className={cn(
                          'px-1 py-0.5',
                          active
                            ? 'overstrike'
                            : 'text-ink-soft hover:bg-band hover:text-ink',
                        )}
                        to={first.path}
                      >
                        <span
                          className={cn('mr-1', active ? '' : 'text-ink-faint')}
                        >
                          {String(index + 1).padStart(2, '0')}
                        </span>
                        {group.label}
                      </Link>
                    )}
                  </span>
                )
              })}
            </div>
            {activeGroup && (
              <div className="scrollbar-thin border-rule-soft flex items-center gap-0 overflow-x-auto border-t border-dashed py-1 font-mono text-[10px] tracking-[0.1em] uppercase">
                <span className="text-ink-faint mr-3 flex-none">
                  {'└'.padEnd(2, '─')} {String(groupIndex + 1).padStart(2, '0')}
                </span>
                {activeGroup.items.map((item, index) => {
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
                                'mr-4 flex-none px-1 py-0.5 whitespace-nowrap',
                                active
                                  ? 'overstrike'
                                  : 'text-ink-soft hover:bg-band hover:text-ink',
                              )}
                              to={item.path}
                            />
                          }
                        >
                          <span className="text-ink-faint mr-1">
                            {groupIndex + 1}.{index + 1}
                          </span>
                          {item.label}
                        </TooltipTrigger>
                        <TooltipContent side="bottom">
                          {item.description}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  )
                })}
              </div>
            )}
          </nav>
        </header>

        {/* Sheet body */}
        <main className="min-h-0 flex-1 overflow-y-auto">
          {pageUnavailable ? (
            <UnavailablePage label={activeItem?.label ?? 'This page'} />
          ) : (
            children
          )}
        </main>
      </div>

      {/* Right sprocket rail */}
      <aside
        aria-hidden="true"
        className="sprocket border-rule-soft bg-band/60 hidden w-9 flex-none border-l sm:block"
      />

      {searchOpen && (
        <Suspense fallback={null}>
          <CommandPalette onClose={() => setSearchOpen(false)} />
        </Suspense>
      )}
    </div>
  )
}

function UnavailablePage({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="border-rule max-w-sm border p-6 text-center">
        <h1 className="stamp text-ink text-xs">{label} is not available</h1>
        <p className="text-ink-soft mt-2 font-mono text-[11px]">
          This surface is disabled by the current lakehouse configuration.
          Enable the capability that provides it to bring it online.
        </p>
      </div>
    </div>
  )
}
