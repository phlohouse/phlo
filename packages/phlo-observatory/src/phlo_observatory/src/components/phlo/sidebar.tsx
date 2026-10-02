/** Renders desktop navigation, environment selection, and quick actions. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { CheckIcon, ChevronDownIcon, SearchIcon } from 'lucide-react'
import { navItems } from './nav-items'
import { Dot } from './status'
import { ThemeSwitch } from './theme-switch'
import { useCommandPalette } from './command-palette'
import type { ObservatoryServiceList } from '@/lib/data/api/client'
import type { Env } from '@/lib/data/types'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/menu'
import { Kbd } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-foreground text-[13px] font-semibold text-background',
        className,
      )}
    >
      φ
    </span>
  )
}

export function EnvPill({ env }: { env: Env }) {
  return (
    <span
      className={cn(
        'rounded-[5px] border px-1.5 py-px text-xs',
        env === 'staging'
          ? 'border-warn bg-warn-soft font-medium text-warn-ink'
          : 'border-border-strong text-muted-foreground',
      )}
    >
      {env}
    </span>
  )
}

/** Brand + environment switcher. Anything that isn't prod is amber. */
export function EnvSwitcher({ env }: { env: Env }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1 text-foreground outline-none hover:bg-nav-hover focus-visible:outline-2 focus-visible:outline-ring">
        <BrandMark />
        <span className="text-[15px] font-semibold">phlo</span>
        <EnvPill env={env} />
        <ChevronDownIcon className="ml-auto size-3.5 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-[280px]">
        <DropdownMenuLabel>Switch environment</DropdownMenuLabel>
        <DropdownMenuItem
          render={<Link to="/" search={{ env: 'prod' }} />}
          className={cn('items-start', env === 'prod' && 'bg-soft')}
        >
          <Dot tone="neutral" size="md" className="mt-1.5 bg-text-3" />
          <span className="flex flex-1 flex-col gap-0.5">
            <span className="flex items-center gap-2 font-medium">prod</span>
            <span className="text-[12.5px] text-muted-foreground">
              Connected API environment
            </span>
          </span>
          {env === 'prod' ? (
            <CheckIcon className="mt-1 size-3.5 text-link" />
          ) : null}
        </DropdownMenuItem>
        <DropdownMenuItem
          render={<Link to="/" search={{ env: 'staging' }} />}
          className={cn('items-start', env === 'staging' && 'bg-soft')}
        >
          <Dot tone="warn" size="md" className="mt-1.5" />
          <span className="flex flex-1 flex-col gap-0.5">
            <span className="flex items-center gap-2 font-medium">staging</span>
            <span className="text-[12.5px] text-muted-foreground">
              Connected API environment
            </span>
          </span>
          {env === 'staging' ? (
            <CheckIcon className="mt-1 size-3.5 text-link" />
          ) : null}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function QuickActionsButton() {
  const { setOpen } = useCommandPalette()
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      className="flex h-9 w-full cursor-pointer items-center gap-2.5 rounded-lg border border-border bg-card px-2.5 text-sm text-text-2 hover:bg-raised"
    >
      <SearchIcon className="size-4" />
      <span>Quick actions</span>
      <Kbd className="ml-auto">⌘K</Kbd>
    </button>
  )
}

const itemCls =
  'flex h-[34px] items-center gap-2.5 rounded-[7px] px-2.5 text-sm text-text-2 hover:bg-nav-hover hover:text-foreground'
const activeCls = 'bg-nav-on text-foreground hover:bg-nav-on'

export function Sidebar({
  env,
  openIncidentCount,
  services,
  identity,
}: {
  env: Env
  openIncidentCount: number | null
  services: ObservatoryServiceList['items']
  identity: { subject: string; email: string | null; roles: Array<string> }
}) {
  const staging = env === 'staging'
  const calm = openIncidentCount === 0
  const [showAllServices, setShowAllServices] = React.useState(false)
  const observedServices = services.filter(
    (service) => service.status !== 'unknown',
  )
  const visibleServices = showAllServices ? services : observedServices
  return (
    <nav
      aria-label="Primary"
      className="flex h-full w-[248px] shrink-0 flex-col gap-[18px] px-2.5 py-3.5"
    >
      <EnvSwitcher env={env} />
      <QuickActionsButton />

      <div className="flex flex-col gap-0.5">
        {navItems.map(({ to, label, Icon, exact }) => {
          return (
            <Link
              key={to}
              to={to}
              search={{ env }}
              activeOptions={{ exact }}
              className={itemCls}
              activeProps={{ className: activeCls, 'aria-current': 'page' }}
            >
              <Icon className="size-4 shrink-0" strokeWidth={1.7} />
              <span className="min-w-0 truncate">{label}</span>
              {to === '/incidents' &&
              openIncidentCount !== null &&
              openIncidentCount > 0 ? (
                <span className="ml-auto rounded-[5px] bg-bad px-1.5 py-px text-xs text-white">
                  {openIncidentCount}
                </span>
              ) : null}
            </Link>
          )
        })}
      </div>

      {calm ? (
        <div className="flex flex-col gap-1.5">
          <div className="px-2.5 text-xs tracking-wide text-muted-foreground">
            Open incidents
          </div>
          <p className="m-0 px-2.5 text-[13.5px] leading-normal text-text-3">
            No active incidents are reported by the connected API.
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-col gap-0.5">
          <div className="px-2.5 pb-1.5 text-xs tracking-wide text-muted-foreground">
            Open incidents
          </div>
          <p className="m-0 px-2.5 text-[13px] text-text-3">
            {openIncidentCount === null ? (
              'Incident status is unavailable.'
            ) : (
              <Link to="/incidents" search={{ env }}>
                {openIncidentCount} reported. View incidents.
              </Link>
            )}
          </p>
        </div>
      )}

      <div className="mt-auto flex flex-col gap-2 border-t border-border px-2.5 pt-3 pb-1">
        <div className="text-xs tracking-wide text-muted-foreground">
          {staging ? 'Services · staging' : 'Services'}
        </div>
        {services.length === 0 ? (
          <p className="m-0 text-[13px] text-text-3">
            No service health observations are available.
          </p>
        ) : null}
        <div className="flex max-h-48 flex-col gap-2 overflow-y-auto">
          {visibleServices.map((s) => {
            const state = s.status
            return (
              <div
                key={s.id}
                className="flex items-center gap-2 text-[13px] text-text-2"
              >
                <Dot
                  tone={
                    state === 'healthy'
                      ? 'ok'
                      : state === 'degraded'
                        ? 'warn'
                        : state === 'unknown'
                          ? 'neutral'
                          : 'bad'
                  }
                />
                {s.id}
                <span className="ml-auto font-mono text-xs text-muted-foreground">
                  {state}
                </span>
              </div>
            )
          })}
        </div>
        {services.length > observedServices.length ? (
          <button
            type="button"
            aria-expanded={showAllServices}
            onClick={() => setShowAllServices((value) => !value)}
            className="cursor-pointer text-left text-xs text-muted-foreground hover:text-foreground"
          >
            {showAllServices
              ? 'Show observed services only'
              : `${services.length - observedServices.length} unobserved · show all`}
          </button>
        ) : null}
        <div
          className="min-w-0 border-t border-line pt-2 text-xs text-muted-foreground"
          aria-label="Signed-in identity"
        >
          <div className="truncate" title={identity.subject}>
            {identity.email ?? identity.subject}
          </div>
          <div>{identity.roles.join(', ') || 'No assigned roles'}</div>
        </div>
        <ThemeSwitch className="mt-1" />
      </div>
    </nav>
  )
}
