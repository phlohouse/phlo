/** Provides keyboard-driven navigation through the Observatory command palette. */
import * as React from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { useNavigate } from '@tanstack/react-router'
import { CornerDownLeftIcon, SearchIcon } from 'lucide-react'
import { navItems } from './nav-items'
import type { Env } from '@/lib/data/types'
import { cn } from '@/lib/utils'
import { Kbd } from '@/components/ui/separator'

type Cmd = {
  id: string
  group: 'Navigation'
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
      <CommandPalette env={env} open={open} onOpenChange={setOpen} />
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
  const all = React.useMemo(() => buildCommands(env), [env])
  const results = React.useMemo(() => {
    const s = q.trim().toLowerCase()
    const r = s ? all.filter((c) => c.label.toLowerCase().includes(s)) : all
    return r.slice(0, 12)
  }, [q, all])

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
                  runAt(active)
                }
              }}
              placeholder="Search pages"
              aria-label="Search"
              className="h-12 flex-1 bg-transparent text-[15px] outline-none placeholder:text-faint"
            />
            <Kbd>esc</Kbd>
          </div>
          <div role="listbox" className="min-h-0 overflow-y-auto p-1.5">
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
                    role="option"
                    aria-selected={i === active}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => runAt(i)}
                    className={cn(
                      'flex h-10 w-full cursor-pointer items-center gap-3 rounded-lg px-3 text-left text-sm text-foreground',
                      i === active && 'bg-primary-soft',
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
                    {i === active ? (
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
