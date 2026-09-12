/**
 * Command palette (⌘K): jumps to Observatory pages and runs entity search
 * against phlo-api. Results deep-link into each surface's query param.
 */
import { useNavigate } from '@tanstack/react-router'
import { CornerDownLeft, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { ObservatorySearchResult } from '@/observatory/api/types'
import { searchObservatoryDirect } from '@/observatory/api/resources'
import { resourceRefHref } from '@/components/observatory/ref-link'
import { ALL_NAV_ITEMS } from '@/components/shell/nav'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Array<ObservatorySearchResult>>([])
  const [searching, setSearching] = useState(false)
  const generation = useRef(0)

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setResults([])
      setSearching(false)
      return
    }
    const token = ++generation.current
    setSearching(true)
    const timeout = window.setTimeout(() => {
      void searchObservatoryDirect({ query: trimmed }).then((result) => {
        if (!generation.current || token !== generation.current) return
        setResults(result.data ?? [])
        setSearching(false)
      })
    }, 200)
    return () => window.clearTimeout(timeout)
  }, [query])

  const open = (href: string) => {
    onClose()
    void navigate({ to: href })
  }

  return (
    <div
      aria-label="Command search"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh]"
      role="dialog"
    >
      <button
        aria-label="Close search"
        className="fixed inset-0 bg-black/50 supports-backdrop-filter:backdrop-blur-sm"
        onClick={onClose}
        type="button"
      />
      <div className="ring-foreground/15 bg-popover relative w-full max-w-xl overflow-hidden rounded-lg shadow-2xl ring-1">
        <Command shouldFilter={false}>
          <CommandInput
            autoFocus
            onValueChange={setQuery}
            placeholder="Jump to a page or search the lakehouse…"
            value={query}
          />
          <CommandList>
            <CommandEmpty>
              {searching ? 'Searching…' : 'No matches.'}
            </CommandEmpty>
            <CommandGroup heading="Pages">
              {ALL_NAV_ITEMS.filter((item) =>
                `${item.label} ${item.id}`
                  .toLowerCase()
                  .includes(query.trim().toLowerCase()),
              ).map((item) => (
                <CommandItem
                  key={item.id}
                  onSelect={() => open(item.path)}
                  value={`page:${item.id}`}
                >
                  {item.icon && <item.icon />}
                  <span>{item.label}</span>
                  <span className="text-muted-foreground ml-auto font-mono text-[10px]">
                    {item.path}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
            {results.length > 0 && (
              <CommandGroup heading="Lakehouse">
                {results.map((result) => {
                  const href = result.href ?? searchResultHref(result)
                  return (
                    <CommandItem
                      disabled={!href}
                      key={`${result.kind}:${result.id}`}
                      onSelect={() => href && open(href)}
                      value={`entity:${result.kind}:${result.id}`}
                    >
                      <CornerDownLeft />
                      <span className="truncate">{result.label}</span>
                      <span className="text-muted-foreground ml-auto font-mono text-[10px] uppercase">
                        {result.kind}
                      </span>
                    </CommandItem>
                  )
                })}
              </CommandGroup>
            )}
          </CommandList>
          <div className="text-muted-foreground flex h-8 items-center gap-3 border-t px-3 font-mono text-[10px]">
            {searching && <Loader2 className="size-3 animate-spin" />}
            <span>↑↓ navigate</span>
            <span>↵ open</span>
            <span>esc close</span>
          </div>
        </Command>
      </div>
    </div>
  )
}

function searchResultHref(result: ObservatorySearchResult): string | null {
  if (result.href) return result.href
  return resourceRefHref({
    id: result.id,
    kind: result.kind,
    label: result.label,
  })
}
