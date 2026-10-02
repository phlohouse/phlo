/** Renders an expandable query catalog for inserting tables and columns. */
import * as React from 'react'
import { ChevronDownIcon, ChevronRightIcon, SearchIcon } from 'lucide-react'
import type { SavedQuery } from '@/lib/data/api/query'
import { cn } from '@/lib/utils'

type Catalog = {
  name: string
  truncated: boolean
  schemas: Array<{ name: string; tables: Array<string> }>
}
const row =
  'flex h-7 w-full min-w-0 cursor-pointer items-center gap-1.5 rounded-md px-2 text-left text-[13px] whitespace-nowrap text-text-2 hover:bg-soft'

export function CatalogTree({
  catalog,
  saved,
  selected,
  onSelect,
  onOpenSaved,
  activeSaved,
  className,
}: {
  catalog: Array<Catalog>
  saved: Array<SavedQuery>
  selected: string
  onSelect: (name: string) => void
  onOpenSaved: (id: string) => void
  activeSaved?: string
  className?: string
}) {
  const [open, setOpen] = React.useState(
    () =>
      new Set(
        catalog.flatMap((c) =>
          c.schemas.slice(0, 1).map((s) => `${c.name}.${s.name}`),
        ),
      ),
  )
  const [q, setQ] = React.useState('')
  const needle = q.trim().toLowerCase()
  const toggle = (key: string) =>
    setOpen((old) => {
      const next = new Set(old)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  return (
    <div className={cn('flex min-h-0 flex-col gap-0.5', className)}>
      <label className="mb-2 flex h-8 shrink-0 items-center gap-2 rounded-lg border border-border bg-raised px-2.5 text-muted-foreground focus-within:border-primary">
        <SearchIcon className="size-3.5" aria-hidden />
        <span className="sr-only">Find a table</span>
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Find a table"
          className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-foreground outline-none placeholder:text-faint"
        />
      </label>
      <ul
        role="tree"
        aria-label="Catalog"
        className="m-0 flex list-none flex-col gap-0.5 p-0"
      >
        {catalog.flatMap((c) =>
          c.schemas.map((schema) => {
            const key = `${c.name}.${schema.name}`
            const tables = schema.tables.filter((table) =>
              table.toLowerCase().includes(needle),
            )
            if (
              needle &&
              !schema.name.toLowerCase().includes(needle) &&
              !tables.length
            )
              return null
            const expanded = needle.length > 0 || open.has(key)
            return (
              <li key={key} role="treeitem" aria-expanded={expanded}>
                <button
                  type="button"
                  className={row}
                  onClick={() => toggle(key)}
                >
                  {expanded ? (
                    <ChevronDownIcon className="size-3" />
                  ) : (
                    <ChevronRightIcon className="size-3" />
                  )}
                  <span className="truncate font-medium text-foreground">
                    {schema.name}
                  </span>
                  <span className="ml-auto text-[11.5px] text-muted-foreground">
                    {schema.tables.length}
                  </span>
                </button>
                {expanded ? (
                  <ul role="group" className="m-0 list-none p-0">
                    {tables.map((table) => {
                      const fq = `${schema.name}.${table}`
                      return (
                        <li
                          key={fq}
                          role="treeitem"
                          aria-selected={selected === fq}
                        >
                          <button
                            type="button"
                            className={cn(
                              row,
                              'pl-7 font-mono text-[12.5px]',
                              selected === fq &&
                                'bg-primary-soft text-foreground',
                            )}
                            onClick={() => onSelect(fq)}
                          >
                            {table}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                ) : null}
              </li>
            )
          }),
        )}
      </ul>
      <div
        className="px-2 pt-[18px] pb-1.5 text-xs text-muted-foreground"
        id="saved-queries"
      >
        Saved queries
      </div>
      <ul aria-labelledby="saved-queries" className="m-0 list-none p-0">
        {saved.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className={cn(
                row,
                activeSaved === item.id && 'bg-soft text-foreground',
              )}
              onClick={() => onOpenSaved(item.id)}
            >
              <span className="truncate">{item.name}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
