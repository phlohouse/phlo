/**
 * The palette: ⌘K object search. Every asset, dataset, table, service,
 * check, and operation in the snapshot is a target — picking one focuses
 * the map and opens the inspector. Extension nav items appear as
 * destinations too.
 */
import { useNavigate } from '@tanstack/react-router'
import {
  Boxes,
  CheckCircle2,
  Database,
  Puzzle,
  Search,
  Server,
  Table2,
  Workflow,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type { FocusRef } from './store'
import type { Snapshot } from './snapshot'
import { useObservatoryExtensions } from '@/extensions/registry'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'

interface PaletteTarget {
  focus: FocusRef | null
  icon: ReactNode
  label: string
  sub?: string
  to?: string
}

const ICON_CLASS = 'text-ink-faint size-3.5 flex-none'

export function usePalette() {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen((current) => !current)
      }
      if (event.key === '/' && !isTyping(event.target)) {
        event.preventDefault()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return { open, setOpen }
}

function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  return (
    element?.tagName === 'INPUT' ||
    element?.tagName === 'TEXTAREA' ||
    element?.isContentEditable === true
  )
}

export function ConsolePalette({
  open,
  onOpenChange,
  onFocus,
  snapshot,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onFocus: (focus: FocusRef) => void
  snapshot: Snapshot
}) {
  const { navItems } = useObservatoryExtensions()
  const navigate = useNavigate()

  const targets = useMemo<Array<PaletteTarget>>(() => {
    const list: Array<PaletteTarget> = []
    for (const asset of snapshot.assets.data ?? []) {
      list.push({
        focus: { kind: 'asset', id: asset.id },
        icon: <Workflow className={ICON_CLASS} />,
        label: asset.name,
        sub: asset.group ?? 'asset',
      })
    }
    for (const dataset of snapshot.datasets.data ?? []) {
      list.push({
        focus: { kind: 'dataset', id: dataset.id },
        icon: <Database className={ICON_CLASS} />,
        label: dataset.name,
        sub: `dataset · ${dataset.publication_state}`,
      })
    }
    for (const table of snapshot.tables.data ?? []) {
      list.push({
        focus: { kind: 'table', id: table.id },
        icon: <Table2 className={ICON_CLASS} />,
        label: table.name,
        sub: `table · ${table.namespace ?? table.format ?? ''}`.trim(),
      })
    }
    for (const service of snapshot.services.data ?? []) {
      list.push({
        focus: { kind: 'service', id: service.id },
        icon: <Server className={ICON_CLASS} />,
        label: service.name,
        sub: `service · ${service.status}`,
      })
    }
    for (const check of snapshot.quality.data ?? []) {
      list.push({
        focus: { kind: 'check', id: check.id },
        icon: <CheckCircle2 className={ICON_CLASS} />,
        label: check.name,
        sub: `check · ${check.status}`,
      })
    }
    for (const operation of (snapshot.operations.data ?? []).slice(0, 60)) {
      list.push({
        focus: { kind: 'op', id: operation.id },
        icon: <Boxes className={ICON_CLASS} />,
        label: operation.name,
        sub: `run · ${operation.status}`,
      })
    }
    for (const item of navItems) {
      list.push({
        focus: null,
        icon: <Puzzle className={ICON_CLASS} />,
        label: item.title,
        sub: 'extension',
        to: item.to,
      })
    }
    return list
  }, [snapshot, navItems])

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent
        className="top-[12vh] w-full max-w-xl translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl"
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">Search the lakehouse</DialogTitle>
        <Command>
          <CommandInput placeholder="Find an asset, table, service, run…" />
          <CommandList>
            <CommandEmpty>Nothing in the lakehouse matches.</CommandEmpty>
            <CommandGroup heading="Objects">
              {targets.map((target, index) => (
                <CommandItem
                  key={`${target.focus ? `${target.focus.kind}:${target.focus.id}` : target.to}-${index}`}
                  onSelect={() => {
                    if (target.focus) onFocus(target.focus)
                    if (target.to) void navigate({ to: target.to })
                    onOpenChange(false)
                  }}
                  value={`${target.label} ${target.sub ?? ''}`}
                >
                  {target.icon}
                  <span className="min-w-0 flex-1 truncate">
                    {target.label}
                  </span>
                  {target.sub && (
                    <span className="text-ink-faint ml-2 flex-none font-mono text-[10px]">
                      {target.sub}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  )
}

export function PaletteButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      className="border-rule bg-panel hover:bg-hover text-ink-faint flex h-7 w-52 items-center gap-2 rounded-lg border px-2.5 text-xs transition-colors"
      onClick={onClick}
      type="button"
    >
      <Search className="size-3.5" />
      <span className="flex-1 text-left">Search the lakehouse</span>
      <kbd className="bg-hover rounded px-1 font-mono text-[9px]">⌘K</kbd>
    </button>
  )
}
