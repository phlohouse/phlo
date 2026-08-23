/**
 * Lightweight role-annotated table for observatory index pages. Rows are
 * buttons carrying active/status markers; column widths come from the
 * caller's CSS grid template.
 */
import type { CSSProperties, ReactNode } from 'react'

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
  variant = 'index',
}: {
  columns: Array<ObservatoryTableColumn>
  columnTemplate: string
  empty?: ReactNode
  rows: Array<ObservatoryTableRow>
  variant?: 'index' | 'compact'
}) {
  const style = {
    '--observatory-table-columns': columnTemplate,
  } as CSSProperties

  return (
    <div
      className="phlo-observatory-index-table"
      data-variant={variant}
      role="table"
      style={style}
    >
      <div className="phlo-observatory-index-table-head" role="row">
        {columns.map((column) => (
          <span key={column.key} role="columnheader">
            {column.label}
          </span>
        ))}
      </div>
      {rows.map((row) => (
        <button
          className="phlo-observatory-index-table-row"
          data-active={row.active === true}
          data-status={row.status}
          key={row.key}
          onClick={row.onSelect}
          role="row"
          type="button"
        >
          {row.cells.map((cell, index) => (
            <span
              key={`${row.key}:${columns[index]?.key ?? index}`}
              role="cell"
            >
              {cell}
            </span>
          ))}
        </button>
      ))}
      {rows.length === 0 && empty}
    </div>
  )
}
