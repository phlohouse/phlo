/**
 * Lightweight role-annotated table for observatory index pages. Rows are
 * buttons carrying active/status markers; column widths come from the
 * caller's CSS grid template.
 */
import type { CSSProperties, ReactNode } from 'react'

import { cn } from '@/lib/utils'

export type ObservatoryTableColumn = {
  key: string
  label: ReactNode
}

export type ObservatoryTableRow = {
  key: string
  active?: boolean
  cells: Array<ReactNode>
  onSelect?: () => void
  status?: string
}

export function ObservatoryIndexTable({
  columns,
  columnTemplate,
  empty,
  rows,
}: {
  columns: Array<ObservatoryTableColumn>
  columnTemplate: string
  empty?: ReactNode
  rows: Array<ObservatoryTableRow>
  variant?: 'index' | 'compact'
}) {
  const style = {
    gridTemplateColumns: columnTemplate,
  } as CSSProperties

  return (
    <div role="table">
      <div
        className="text-muted-foreground grid gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase"
        role="row"
        style={style}
      >
        {columns.map((column) => (
          <span key={column.key} role="columnheader">
            {column.label}
          </span>
        ))}
      </div>
      <div className="divide-border divide-y">
        {rows.map((row) => (
          <button
            className={cn(
              'hover:bg-accent/50 grid w-full items-center gap-3 px-3 py-2 text-left transition-colors',
              row.active && 'bg-accent/60 hover:bg-accent/60',
            )}
            data-status={row.status}
            key={row.key}
            onClick={row.onSelect}
            role="row"
            style={style}
            type="button"
          >
            {row.cells.map((cell, index) => (
              <span
                className="min-w-0 truncate"
                key={`${row.key}:${columns[index]?.key ?? index}`}
                role="cell"
              >
                {cell}
              </span>
            ))}
          </button>
        ))}
      </div>
      {rows.length === 0 && empty}
    </div>
  )
}
