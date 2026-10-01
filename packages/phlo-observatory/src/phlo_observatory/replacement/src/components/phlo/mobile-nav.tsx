/** Renders the mobile top bar and route tab bar. */
import { Link } from '@tanstack/react-router'
import { SearchIcon } from 'lucide-react'
import { navItems } from './nav-items'
import { BrandMark, EnvPill } from './sidebar'
import { ThemeToggleButton } from './theme-switch'
import { useCommandPalette } from './command-palette'
import type { Env } from '@/lib/data/types'

/** Phone header: brand, environment, search, theme. Shown below the lg breakpoint. */
export function MobileTopBar({ env }: { env: Env }) {
  const { setOpen } = useCommandPalette()
  return (
    <header className="flex h-14 shrink-0 items-center gap-2.5 border-b border-line bg-card px-4 lg:hidden">
      <Link
        to="/"
        search={{ env }}
        className="flex items-center gap-2.5 text-foreground"
      >
        <BrandMark className="size-7 text-sm" />
        <span className="text-[17px] font-semibold">phlo</span>
      </Link>
      <EnvPill env={env} />
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Search and quick actions"
        className="ml-auto inline-flex size-11 cursor-pointer items-center justify-center rounded-lg text-text-3 hover:bg-soft"
      >
        <SearchIcon className="size-[18px]" />
      </button>
      <ThemeToggleButton />
    </header>
  )
}

/** Bottom tab bar on phones: Home, Incidents, Assets, Pipelines. */
export function MobileTabBar({
  env,
  openIncidents,
}: {
  env: Env
  openIncidents: number | null
}) {
  return (
    <nav
      aria-label="Primary"
      className="grid h-16 shrink-0 grid-cols-4 border-t border-line bg-card pb-[env(safe-area-inset-bottom)] lg:hidden"
    >
      {navItems
        .filter((n) => n.mobile)
        .map(({ to, label, Icon, exact }) => (
          <Link
            key={to}
            to={to}
            search={{ env }}
            activeOptions={{ exact }}
            className="relative flex flex-col items-center justify-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            activeProps={{
              className: 'text-link hover:text-link',
              'aria-current': 'page',
            }}
          >
            <span className="relative">
              <Icon className="size-5" strokeWidth={1.7} />
              {to === '/incidents' &&
              openIncidents !== null &&
              openIncidents > 0 ? (
                <span className="absolute -top-1.5 -right-2.5 min-w-4 rounded-full bg-bad px-1 text-center text-[10px] leading-4 text-white">
                  {openIncidents}
                </span>
              ) : null}
            </span>
            {to === '/' ? 'Home' : label}
          </Link>
        ))}
    </nav>
  )
}
