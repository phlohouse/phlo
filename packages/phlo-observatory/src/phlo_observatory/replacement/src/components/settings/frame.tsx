/** Frames settings pages with workspace and environment subnavigation. */
import * as React from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { cn } from '@/lib/utils'

type NavItem = {
  label: string
  to: string
  hash?: string
  match?: (path: string, hash: string) => boolean
}

const workspace: Array<NavItem> = [
  {
    label: 'Lakehouse',
    to: '/settings',
    match: (p) => p === '/settings' || p === '/settings/',
  },
  {
    label: 'Members',
    to: '/settings/members',
    match: (p, h) =>
      p.startsWith('/settings/members') && h !== 'service-accounts',
  },
  {
    label: 'Service accounts',
    to: '/settings/members',
    hash: 'service-accounts',
    match: (p, h) =>
      p.startsWith('/settings/members') && h === 'service-accounts',
  },
  {
    label: 'Audit log',
    to: '/settings/audit-log',
    match: (p) => p.startsWith('/settings/audit-log'),
  },
]
const environments: Array<NavItem> = [
  { label: 'prod', to: '/' },
  { label: 'staging', to: '/staging' },
]

function SubnavLink({ item, on }: { item: NavItem; on: boolean }) {
  const search = useRouterState({ select: (s) => s.location.search })
  return (
    <Link
      to={item.to}
      hash={item.hash}
      search={search}
      aria-current={on ? 'page' : undefined}
      className={cn(
        'flex h-10 shrink-0 items-center rounded-md px-2.5 text-[13.5px] whitespace-nowrap text-text-2 hover:bg-soft hover:text-foreground lg:h-8',
        on && 'bg-soft text-foreground',
      )}
    >
      {item.label}
    </Link>
  )
}

/**
 * Settings frame: the page header on top, then [sub-navigation | content]. The sub-nav is a
 * 196px column on desktop and a horizontally scrolling tab row on phones.
 */
export function SettingsFrame({
  header,
  children,
  className,
}: {
  header: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  const { pathname, hash } = useRouterState({
    select: (s) => ({ pathname: s.location.pathname, hash: s.location.hash }),
  })
  const h = hash.replace(/^#/, '')

  return (
    <>
      {header}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <nav
          aria-label="Settings sections"
          className="flex shrink-0 gap-1 overflow-x-auto border-b border-line px-3 py-2 [scrollbar-width:none] lg:w-[196px] lg:flex-col lg:gap-0.5 lg:overflow-visible lg:border-r lg:border-b-0 lg:px-2.5 lg:py-4"
        >
          <div className="hidden px-2.5 pb-1.5 text-xs text-muted-foreground lg:block">
            Workspace
          </div>
          {workspace.map((i) => (
            <SubnavLink key={i.label} item={i} on={!!i.match?.(pathname, h)} />
          ))}
          <div className="hidden px-2.5 pt-4 pb-1.5 text-xs text-muted-foreground lg:block">
            Environments
          </div>
          <span
            aria-hidden
            className="mx-1 my-2.5 w-px shrink-0 bg-border lg:hidden"
          />
          {environments.map((i) => (
            <SubnavLink key={i.label} item={i} on={false} />
          ))}
        </nav>
        <div className={cn('flex min-h-0 min-w-0 flex-1 flex-col', className)}>
          {children}
        </div>
      </div>
    </>
  )
}
