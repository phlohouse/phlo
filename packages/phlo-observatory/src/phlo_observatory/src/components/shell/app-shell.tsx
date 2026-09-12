/**
 * Observatory app shell. A left navigation rail (workspace header, search,
 * grouped sections with icon+label items and status dots) beside a main
 * column carrying the report surface. Capability pages gate nav visibility;
 * extension-contributed pages append a group at the end.
 */
import { Link, useRouterState } from '@tanstack/react-router'
import { Boxes, ChevronLeft, Search } from 'lucide-react'
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
      icon: Boxes,
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
    <div className="bg-canvas text-ink flex h-svh overflow-hidden">
      {/* Navigation rail */}
      <aside className="bg-panel border-rule hidden w-60 flex-none flex-col border-r sm:flex">
        <div className="flex h-12 flex-none items-center gap-2.5 px-4">
          <span className="bg-raised border-rule flex size-6 items-center justify-center rounded-md border">
            <Boxes className="text-blue size-3.5" />
          </span>
          <Link className="text-ink text-[13px] font-semibold" to="/">
            Phlo Observatory
          </Link>
          <span className="flex-1" />
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger render={<span className="flex items-center" />}>
                <HealthDot state={health?.state ?? 'unknown'} />
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {health?.message ?? 'Waiting for lakehouse status'}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>

        <div className="flex-none px-3 pb-2">
          <button
            aria-expanded={searchOpen}
            aria-haspopup="dialog"
            className="border-rule bg-raised text-ink-soft hover:border-foreground/20 hover:text-ink flex h-8 w-full items-center gap-2 rounded-lg border px-2.5 text-xs transition-colors"
            onClick={() => setSearchOpen(true)}
            type="button"
          >
            <Search className="size-3.5" />
            <span className="flex-1 text-left">Search…</span>
            <kbd className="border-rule text-ink-faint rounded border px-1 font-mono text-[10px]">
              ⌘K
            </kbd>
          </button>
        </div>

        <nav
          aria-label="Observatory"
          className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-3 pb-3"
        >
          {groups.map((group) => (
            <div className="pt-4 first:pt-2" key={group.id}>
              <div className="text-ink-faint px-2 pb-1.5 text-[11px] font-medium">
                {group.label}
              </div>
              <ul className="flex flex-col gap-0.5">
                {group.items.map((item) => {
                  const active =
                    hydrated &&
                    (item.path === '/'
                      ? pathname === '/'
                      : pathname.startsWith(item.path))
                  const Icon = item.icon
                  return (
                    <li key={item.id}>
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <Link
                                aria-current={active ? 'page' : undefined}
                                className={cn(
                                  'flex h-8 items-center gap-2.5 rounded-lg px-2 text-[13px] transition-colors',
                                  active
                                    ? 'bg-selected text-ink font-medium'
                                    : 'text-ink-soft hover:bg-hover hover:text-ink',
                                )}
                                to={item.path}
                              />
                            }
                          >
                            {Icon && (
                              <Icon
                                className={cn(
                                  'size-4 flex-none',
                                  active ? 'text-blue' : 'text-ink-faint',
                                )}
                              />
                            )}
                            <span className="min-w-0 flex-1 truncate">
                              {item.label}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="right">
                            {item.description}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="border-rule flex-none border-t p-3">
          <div className="bg-raised border-rule flex items-center gap-2.5 rounded-lg border px-3 py-2.5">
            <HealthDot state={health?.state ?? 'unknown'} />
            <div className="min-w-0 flex-1">
              <div className="text-ink text-xs font-medium">Lakehouse</div>
              <div className="text-ink-faint truncate text-[11px]">
                {health?.message ?? 'Connecting…'}
              </div>
            </div>
          </div>
        </div>
      </aside>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-rule flex h-12 flex-none items-center gap-2 border-b px-4">
          <Link
            aria-label="Back to overview"
            className="text-ink-faint hover:bg-hover hover:text-ink flex size-7 items-center justify-center rounded-lg transition-colors"
            to="/"
          >
            <ChevronLeft className="size-4" />
          </Link>
          <nav
            aria-label="Breadcrumb"
            className="text-ink-soft flex items-center gap-1.5 text-[13px]"
          >
            {activeGroup && <span>{activeGroup.label}</span>}
            {activeGroup && activeItem && (
              <span aria-hidden="true" className="text-ink-faint">
                /
              </span>
            )}
            {activeItem && (
              <span className="text-ink font-medium">{activeItem.label}</span>
            )}
          </nav>
          <div className="flex-1" />
          <button
            className="border-rule text-ink-soft hover:border-foreground/20 hover:text-ink flex h-7 items-center gap-1.5 rounded-lg border px-2.5 text-xs transition-colors sm:hidden"
            onClick={() => setSearchOpen(true)}
            type="button"
          >
            <Search className="size-3.5" />
          </button>
        </header>

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

function UnavailablePage({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="bg-panel border-rule max-w-sm rounded-xl border p-6 text-center">
        <h1 className="text-ink text-sm font-semibold">
          {label} is not available
        </h1>
        <p className="text-ink-soft mt-2 text-xs">
          This surface is disabled by the current lakehouse configuration.
          Enable the capability that provides it to bring it online.
        </p>
      </div>
    </div>
  )
}
