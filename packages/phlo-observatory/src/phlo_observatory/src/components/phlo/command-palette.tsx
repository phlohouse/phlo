/** Provides keyboard-driven navigation through the Observatory command palette. */
import * as React from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { useNavigate } from '@tanstack/react-router'
import {
  CornerDownLeftIcon,
  DatabaseIcon,
  FileWarningIcon,
  SearchIcon,
  WorkflowIcon,
} from 'lucide-react'
import { navItems } from './nav-items'
import type { Env } from '@/lib/data/types'
import { getAssetList } from '@/lib/data/api/assets'
import { getPipelineList } from '@/lib/data/api/pipelines'
import { getIncidentList } from '@/lib/data/api/incidents'
import { cn } from '@/lib/utils'
import { Kbd } from '@/components/ui/separator'

type Cmd = {
  id: string
  group: 'Assets' | 'Jobs' | 'Incidents' | 'Actions' | 'Navigation'
  label: string
  mono?: boolean
  hint?: string
  icon: React.ReactNode
  run: (nav: ReturnType<typeof useNavigate>) => void
}

const PaletteContext = React.createContext<{
  open: boolean
  setOpen: (o: boolean) => void
}>({
  open: false,
  setOpen: () => {},
})

export const useCommandPalette = () => React.useContext(PaletteContext)

export function CommandPaletteProvider({
  env,
  children,
}: {
  env: Env
  children: React.ReactNode
}) {
  const [open, setOpen] = React.useState(false)
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return (
    <PaletteContext.Provider value={{ open, setOpen }}>
      {children}
      <CommandPalette key={env} env={env} open={open} onOpenChange={setOpen} />
    </PaletteContext.Provider>
  )
}

const iconCls =
  'inline-flex size-6 shrink-0 items-center justify-center rounded-md'

function buildCommands(env: Env): Array<Cmd> {
  return navItems.map(({ to, label, Icon }) => ({
    id: to,
    group: 'Navigation',
    label,
    icon: (
      <span className={cn(iconCls, 'bg-soft text-muted-foreground')}>
        <Icon className="size-3.5" />
      </span>
    ),
    run: (nav) => nav({ to, search: { env } }),
  }))
}

function Highlight({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>
  const i = text.toLowerCase().indexOf(q.toLowerCase())
  if (i < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, i)}
      <mark className="bg-transparent font-semibold text-link">
        {text.slice(i, i + q.length)}
      </mark>
      {text.slice(i + q.length)}
    </>
  )
}

function CommandPalette({
  env,
  open,
  onOpenChange,
}: {
  env: Env
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const navigate = useNavigate()
  const [q, setQ] = React.useState('')
  const [active, setActive] = React.useState(0)
  const [targets, setTargets] = React.useState<Array<Cmd>>([])
  const [targetError, setTargetError] = React.useState<string>()
  React.useEffect(() => {
    setTargets([])
    setTargetError(undefined)
    if (!open) return
    let stopped = false
    void Promise.all([
      getAssetList({ data: env }),
      getPipelineList({ data: env }),
      getIncidentList({ data: env }),
    ])
      .then(([assets, pipelines, incidents]) => {
        if (stopped) return
        const commands: Array<Cmd> = [
          ...assets.items.map((asset): Cmd => ({
            id: `asset:${asset.id}`,
            group: 'Assets',
            label: asset.id,
            mono: true,
            icon: (
              <span className={cn(iconCls, 'bg-soft text-muted-foreground')}>
                <DatabaseIcon className="size-3.5" />
              </span>
            ),
            run: (nav) =>
              nav({
                to: '/assets/$assetId',
                params: { assetId: asset.id },
                search: { env },
              }),
          })),
          ...pipelines.jobs.map((job): Cmd => ({
            id: `job:${job.id}`,
            group: 'Jobs',
            label: job.id,
            mono: true,
            icon: (
              <span className={cn(iconCls, 'bg-soft text-muted-foreground')}>
                <WorkflowIcon className="size-3.5" />
              </span>
            ),
            run: (nav) =>
              nav({
                to: '/pipelines/$jobName',
                params: { jobName: job.id },
                search: { env },
              }),
          })),
          ...incidents.incidents
            .filter((item) => item.status !== 'resolved')
            .map((incident): Cmd => ({
              id: `incident:${incident.id}`,
              group: 'Incidents',
              label: incident.title,
              icon: (
                <span className={cn(iconCls, 'bg-soft text-muted-foreground')}>
                  <FileWarningIcon className="size-3.5" />
                </span>
              ),
              run: (nav) =>
                nav({
                  to: '/incidents/$incidentId',
                  params: { incidentId: incident.id },
                  search: { env },
                }),
            })),
        ]
        setTargets(commands)
      })
      .catch((error) => {
        if (!stopped)
          setTargetError(
            error instanceof Error
              ? error.message
              : 'Search targets are unavailable.',
          )
      })
    return () => {
      stopped = true
    }
  }, [open, env])
  const all = React.useMemo(
    () => [...targets, ...buildCommands(env)],
    [targets, env],
  )
  const results = React.useMemo(() => {
    const s = q.trim().toLowerCase()
    const r = s ? all.filter((c) => c.label.toLowerCase().includes(s)) : all
    return r.slice(0, 12)
  }, [q, all])
  const activeIndex = results.length ? Math.min(active, results.length - 1) : -1

  React.useEffect(() => setActive(0), [q])
  React.useEffect(() => {
    if (!open) setQ('')
  }, [open])

  const runAt = (i: number) => {
    const c = results[i]
    if (!c) return
    onOpenChange(false)
    c.run(navigate)
  }

  let lastGroup = ''
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-scrim" />
        <DialogPrimitive.Popup
          aria-label="Quick actions"
          className="fixed top-[12vh] left-1/2 z-50 flex max-h-[70vh] w-[calc(100vw-2rem)] max-w-[640px] -translate-x-1/2 flex-col overflow-hidden rounded-[14px] bg-card shadow-dialog outline-none"
        >
          <div className="flex items-center gap-2.5 border-b border-line px-4">
            <SearchIcon className="size-4 text-muted-foreground" />
            <input
              autoFocus
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={open}
              aria-controls="command-palette-options"
              aria-activedescendant={
                activeIndex >= 0 ? `command-option-${activeIndex}` : undefined
              }
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  setActive((a) => Math.min(a + 1, results.length - 1))
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  setActive((a) => Math.max(a - 1, 0))
                } else if (e.key === 'Enter') {
                  e.preventDefault()
                  runAt(activeIndex)
                }
              }}
              placeholder="Search pages, assets and jobs"
              aria-label="Search"
              className="h-12 flex-1 bg-transparent text-[15px] outline-none placeholder:text-faint"
            />
            <Kbd>esc</Kbd>
          </div>
          {targetError ? (
            <p
              role="status"
              className="m-0 px-4 py-2 text-xs text-muted-foreground"
            >
              Asset, job and incident search is unavailable. {targetError} Page
              navigation remains available.
            </p>
          ) : null}
          <div
            id="command-palette-options"
            role="listbox"
            aria-label="Search results"
            className="min-h-0 overflow-y-auto p-1.5"
          >
            {results.length === 0 ? (
              <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                Nothing matches "{q}"
              </div>
            ) : null}
            {results.map((c, i) => {
              const header = c.group !== lastGroup ? c.group : null
              lastGroup = c.group
              return (
                <React.Fragment key={c.id}>
                  {header ? (
                    <div className="px-3 pt-2.5 pb-1 text-xs tracking-wide text-muted-foreground">
                      {header}
                    </div>
                  ) : null}
                  <button
                    type="button"
                    id={`command-option-${i}`}
                    role="option"
                    aria-selected={i === activeIndex}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => runAt(i)}
                    className={cn(
                      'flex h-10 w-full cursor-pointer items-center gap-3 rounded-lg px-3 text-left text-sm text-foreground',
                      i === activeIndex && 'bg-primary-soft',
                    )}
                  >
                    {c.icon}
                    <span
                      className={cn(
                        'min-w-0 flex-1 truncate',
                        c.mono && 'font-mono text-[13px]',
                      )}
                    >
                      <Highlight text={c.label} q={q.trim()} />
                    </span>
                    {c.hint ? (
                      <span className="text-xs text-muted-foreground">
                        {c.hint}
                      </span>
                    ) : null}
                    {i === activeIndex ? (
                      <CornerDownLeftIcon className="size-3.5 text-muted-foreground" />
                    ) : null}
                  </button>
                </React.Fragment>
              )
            })}
          </div>
          <div className="flex items-center gap-4 border-t border-line bg-raised px-4 py-2 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <Kbd>↑</Kbd>
              <Kbd>↓</Kbd> Move
            </span>
            <span className="flex items-center gap-1.5">
              <Kbd>↵</Kbd> Open
            </span>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
