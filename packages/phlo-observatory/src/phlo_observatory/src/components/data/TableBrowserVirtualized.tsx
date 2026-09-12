/**
 * Virtualized Table Browser Component
 *
 * ADR: 0020-observatory-table-browser-improvements.md
 * Bead: phlo-13a
 *
 * Features:
 * - Virtualized list rendering for 500+ tables
 * - Enhanced search with fuzzy matching on name/schema/layer
 * - Keyboard navigation (arrow keys, Enter, Escape)
 * - List density from existing Settings
 * - Selected table highlighting
 */

import { useVirtualizer } from '@tanstack/react-virtual'
import {
  ChevronDown,
  ChevronRight,
  Database,
  Folder,
  Search,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { IcebergTable } from '@/server/iceberg.server'
import { Input } from '@/components/ui/input'
import { useObservatorySettings } from '@/hooks/useObservatorySettings'
import { cn } from '@/lib/utils'

// ─────────────────────────────────────────────────────────────────────────────
// Types and Constants
// ─────────────────────────────────────────────────────────────────────────────

interface TableBrowserVirtualizedProps {
  tables: Array<IcebergTable>
  error: string | null
  selectedTable?: string | null
  selectedSchema?: string | null
  onSelectTable: (table: IcebergTable) => void
}

interface LayerConfig {
  key: IcebergTable['layer']
  label: string
  color: string
}

const LAYERS: Array<LayerConfig> = [
  { key: 'bronze', label: 'Bronze (Raw)', color: 'text-amber-400' },
  { key: 'silver', label: 'Silver (Staged)', color: 'text-muted-foreground' },
  { key: 'gold', label: 'Gold (Curated)', color: 'text-primary' },
  { key: 'publish', label: 'Publish (Marts)', color: 'text-emerald-400' },
  { key: 'unknown', label: 'Other', color: 'text-muted-foreground' },
]

// ─────────────────────────────────────────────────────────────────────────────
// Search Scoring
// ─────────────────────────────────────────────────────────────────────────────

function matchScore(table: IcebergTable, query: string): number {
  if (!query) return 100 // Show all when no query
  const q = query.toLowerCase()
  const name = table.name.toLowerCase()
  const schema = table.schema.toLowerCase()
  const layer = table.layer.toLowerCase()

  if (name === q) return 100 // Exact name match
  if (name.startsWith(q)) return 80 // Prefix match
  if (name.includes(q)) return 60 // Substring match
  if (schema.includes(q)) return 40 // Schema match
  if (layer.includes(q)) return 20 // Layer match

  // Multi-word search: "bronze events" matches bronze layer + events in name
  const words = q.split(/\s+/).filter(Boolean)
  if (words.length > 1) {
    const combined = `${layer} ${schema} ${name}`
    if (words.every((w) => combined.includes(w))) return 50
  }

  return 0
}

// ─────────────────────────────────────────────────────────────────────────────
// Grouping Logic
// ─────────────────────────────────────────────────────────────────────────────

interface GroupedItem {
  type: 'header' | 'table'
  key: string
  label?: string
  color?: string
  count?: number
  table?: IcebergTable
}

function groupByLayer(tables: Array<IcebergTable>): Array<GroupedItem> {
  const byLayer: Record<string, Array<IcebergTable>> = {}
  for (const t of tables) {
    if (!byLayer[t.layer]) byLayer[t.layer] = []
    byLayer[t.layer].push(t)
  }

  const items: Array<GroupedItem> = []
  for (const layer of LAYERS) {
    const layerTables = byLayer[layer.key] || []
    if (layerTables.length === 0) continue
    items.push({
      type: 'header',
      key: `header-${layer.key}`,
      label: layer.label,
      color: layer.color,
      count: layerTables.length,
    })
    for (const t of layerTables) {
      items.push({ type: 'table', key: t.fullName, table: t })
    }
  }
  return items
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function TableBrowserVirtualized({
  tables,
  error,
  selectedTable,
  selectedSchema,
  onSelectTable,
}: TableBrowserVirtualizedProps) {
  const { settings } = useObservatorySettings()
  const [search, setSearch] = useState('')
  const [focusedIndex, setFocusedIndex] = useState<number>(-1)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    new Set(['bronze', 'silver', 'gold', 'publish', 'unknown']),
  )

  const parentRef = useRef<HTMLDivElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)

  // Row height from existing settings
  const rowHeight = settings.ui.density === 'compact' ? 28 : 36

  // Filter and score tables
  const filteredTables = useMemo(() => {
    if (!search.trim()) return tables

    const matches: Array<{ table: IcebergTable; score: number }> = []
    for (const table of tables) {
      const score = matchScore(table, search)
      if (score > 0) {
        matches.push({ table, score })
      }
    }

    return matches
      .slice()
      .sort((a, b) => b.score - a.score)
      .map((match) => match.table)
  }, [tables, search])

  // Group tables into flat list with headers (always by layer)
  const groupedItems = useMemo(() => {
    const grouped = groupByLayer(filteredTables)

    // Filter out collapsed groups
    return grouped.filter((item) => {
      if (item.type === 'header') return true
      const headerKey = item.table?.layer || ''
      return expandedGroups.has(headerKey)
    })
  }, [filteredTables, expandedGroups])

  // Get only table items for keyboard navigation
  const tableItems = useMemo(
    () => groupedItems.filter((item) => item.type === 'table'),
    [groupedItems],
  )

  // Virtualizer
  const rowVirtualizer = useVirtualizer({
    count: groupedItems.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 10,
  })

  // Toggle group expansion
  const toggleGroup = useCallback((groupKey: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(groupKey)) {
        next.delete(groupKey)
      } else {
        next.add(groupKey)
      }
      return next
    })
  }, [])

  // Keyboard navigation
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (tableItems.length === 0) return

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setFocusedIndex((prev) =>
            prev < tableItems.length - 1 ? prev + 1 : prev,
          )
          break
        case 'ArrowUp':
          e.preventDefault()
          setFocusedIndex((prev) => (prev > 0 ? prev - 1 : prev))
          break
        case 'Enter':
          e.preventDefault()
          if (focusedIndex >= 0 && focusedIndex < tableItems.length) {
            const item = tableItems[focusedIndex]
            if (item.table) onSelectTable(item.table)
          }
          break
        case 'Escape':
          e.preventDefault()
          setSearch('')
          setFocusedIndex(-1)
          searchInputRef.current?.focus()
          break
      }
    },
    [tableItems, focusedIndex, onSelectTable],
  )

  // Scroll focused item into view
  useEffect(() => {
    if (focusedIndex >= 0 && focusedIndex < tableItems.length) {
      const focusedItem = tableItems[focusedIndex]
      const virtualIndex = groupedItems.findIndex(
        (item) => item.key === focusedItem.key,
      )
      if (virtualIndex >= 0) {
        rowVirtualizer.scrollToIndex(virtualIndex, { align: 'auto' })
      }
    }
  }, [focusedIndex, tableItems, groupedItems, rowVirtualizer])

  // Error state
  if (error) {
    return (
      <div className="p-4 text-red-400 text-sm">
        <p className="font-medium mb-1">Failed to load tables</p>
        <p className="text-red-400/70">Lakehouse API is unavailable.</p>
      </div>
    )
  }

  return (
    <div
      className="flex flex-col h-full"
      onKeyDown={handleKeyDown}
      role="listbox"
      aria-label="Tables"
      tabIndex={0}
    >
      {/* Header with search */}
      <div className="p-3 border-b">
        {/* Search input */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            ref={searchInputRef}
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setFocusedIndex(-1)
            }}
            placeholder="Search tables..."
            className="pl-9 pr-20"
          />
          {/* Table count */}
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
            {filteredTables.length} tables
          </span>
        </div>
      </div>

      {/* Virtualized table list */}
      <div ref={parentRef} className="flex-1 overflow-y-auto touch-scroll">
        <div
          style={{
            height: `${rowVirtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {rowVirtualizer.getVirtualItems().map((virtualRow) => {
            const item = groupedItems[virtualRow.index]
            if (!item) return null

            if (item.type === 'header') {
              const groupKey = item.label?.split(' ')[0].toLowerCase() || ''
              const isExpanded = expandedGroups.has(groupKey)

              return (
                <button
                  key={item.key}
                  onClick={() => toggleGroup(groupKey)}
                  className="absolute top-0 left-0 w-full flex items-center gap-2 px-3 hover:bg-muted/50 text-sm"
                  style={{
                    height: `${virtualRow.size}px`,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  {isExpanded ? (
                    <ChevronDown className="size-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="size-4 text-muted-foreground" />
                  )}
                  <Folder className={`size-4 ${item.color}`} />
                  <span className={item.color}>{item.label}</span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {item.count}
                  </span>
                </button>
              )
            }

            // Table item
            const table = item.table!
            const isSelected =
              selectedTable === table.name && selectedSchema === table.schema
            const tableIdx = tableItems.findIndex((t) => t.key === item.key)
            const isFocused = tableIdx === focusedIndex

            return (
              <button
                key={item.key}
                onClick={() => onSelectTable(table)}
                className={cn(
                  'absolute top-0 left-0 w-full flex items-center gap-2 px-3 pl-9 text-sm transition-colors',
                  isSelected
                    ? 'bg-primary/10 text-foreground font-medium'
                    : 'hover:bg-muted/50 text-muted-foreground hover:text-foreground',
                  isFocused && 'border ring-inset border-ink',
                )}
                style={{
                  height: `${virtualRow.size}px`,
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                <Database className="size-4 text-muted-foreground flex-shrink-0" />
                <span className="truncate">{table.name}</span>
              </button>
            )
          })}
        </div>

        {/* Empty state */}
        {filteredTables.length === 0 && (
          <div className="text-center py-8 text-muted-foreground text-sm">
            <Database className="size-8 mx-auto mb-2 opacity-50" />
            <p>{search ? 'No matching tables' : 'No tables found'}</p>
          </div>
        )}
      </div>
    </div>
  )
}
