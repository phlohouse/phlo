/** Lists implemented shortcuts and handles two-key navigation outside editors. */
import * as React from 'react'
import { useNavigate } from '@tanstack/react-router'
import { KeyboardIcon } from 'lucide-react'
import type { Env } from '@/lib/data/types'
import { Input } from '@/components/ui/input'
import { Kbd } from '@/components/ui/separator'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

const destinations = {
  o: { to: '/', label: 'Overview' },
  i: { to: '/incidents', label: 'Incidents' },
  a: { to: '/assets', label: 'Assets' },
  q: { to: '/query', label: 'Query' },
  p: { to: '/pipelines', label: 'Pipelines' },
  b: { to: '/branches', label: 'Branches' },
  s: { to: '/settings', label: 'Settings' },
} as const

export function KeyboardShortcuts({ env }: { env: Env }) {
  const navigate = useNavigate()
  const [open, setOpen] = React.useState(false)
  const [search, setSearch] = React.useState('')
  React.useEffect(() => {
    let prefixAt = 0
    const onKey = (event: KeyboardEvent) => {
      const target = event.target
      const editing =
        target instanceof HTMLElement &&
        !!target.closest(
          'input, textarea, select, [contenteditable="true"], [role="textbox"]',
        )
      if (
        editing ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.defaultPrevented
      ) {
        prefixAt = 0
        return
      }
      if (event.key === '?' && !document.querySelector('[role="dialog"]')) {
        event.preventDefault()
        setOpen(true)
        prefixAt = 0
        return
      }
      if (open || document.querySelector('[role="dialog"]')) return
      const key = event.key.toLowerCase()
      const destination = destinations[key as keyof typeof destinations]
      if (prefixAt && Date.now() - prefixAt < 1200 && destination) {
        event.preventDefault()
        prefixAt = 0
        void navigate({ to: destination.to, search: { env } })
      } else {
        prefixAt = key === 'g' ? Date.now() : 0
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [env, navigate, open])

  const groups = [
    {
      name: 'Everywhere',
      items: [
        ['Quick actions', '⌘ / Ctrl K'],
        ['Show keyboard shortcuts', '?'],
        ...Object.entries(destinations).map(([key, value]) => [
          `Go to ${value.label}`,
          `G then ${key.toUpperCase()}`,
        ]),
      ],
    },
    {
      name: 'Query editor',
      items: [
        ['Run current query', '⌘ / Ctrl Enter'],
        ['Undo', '⌘ / Ctrl Z'],
        ['Accept completion', 'Enter'],
      ],
    },
  ]
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground hover:bg-soft hover:text-foreground"
        aria-label="Keyboard shortcuts"
      >
        <KeyboardIcon className="size-3.5" />
        Keyboard shortcuts<Kbd className="ml-auto">?</Kbd>
      </button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setSearch('')
        }}
      >
        <DialogContent className="max-w-[760px]">
          <DialogHeader className="flex-row items-center justify-between gap-4">
            <DialogTitle className="text-sm font-medium">
              Keyboard shortcuts
            </DialogTitle>
            <Input
              aria-label="Search shortcuts"
              placeholder="Search shortcuts"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="max-w-64"
            />
          </DialogHeader>
          <DialogBody className="grid gap-8 sm:grid-cols-2">
            {groups.map((group) => (
              <section key={group.name}>
                <h2 className="mb-2 text-xs font-normal text-muted-foreground">
                  {group.name}
                </h2>
                <dl>
                  {group.items
                    .filter(([label]) =>
                      label.toLowerCase().includes(search.toLowerCase()),
                    )
                    .map(([label, keys]) => (
                      <div
                        key={label}
                        className="flex items-center justify-between gap-4 border-b border-line py-2 text-sm"
                      >
                        <dt>{label}</dt>
                        <dd>
                          <Kbd>{keys}</Kbd>
                        </dd>
                      </div>
                    ))}
                </dl>
              </section>
            ))}
          </DialogBody>
          <DialogFooter className="justify-between text-xs text-muted-foreground">
            <span>
              Navigation shortcuts are inactive while typing. Ctrl replaces ⌘ on
              Windows and Linux.
            </span>
            <span className="shrink-0">
              <Kbd>esc</Kbd> Close
            </span>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
