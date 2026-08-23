/**
 * TanStack table over raw query rows: virtualized rendering, optional
 * sorting, column pinning, and per-column widths estimated from header and
 * type lengths. Shared by query results and row journey views.
 */
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, MoreVertical, Pin, PinOff } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import type {
  ColumnDef,
  ColumnPinningState,
  SortingState,
} from '@tanstack/react-table'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useObservatorySettings } from '@/hooks/useObservatorySettings'
import { cn } from '@/lib/utils'

type ObservatoryRow = Record<string, unknown>

export interface ObservatoryTableProps {
  columns: Array<string>
  columnTypes?: Array<string>
  rows: Array<ObservatoryRow>
  getRowId?: (row: ObservatoryRow, index: number) => string
  onRowClick?: (row: ObservatoryRow) => void

  monospace?: boolean
  maxHeightClassName?: string
  containerClassName?: string

  enableSorting?: boolean
  enableColumnResizing?: boolean
  enableColumnPinning?: boolean

  formatCellValue?: (
    value: unknown,
    columnId: string,
    columnType?: string,
  ) => string
}

function isNumericType(columnType: string | undefined) {
  if (!columnType) return false
  const normalized = columnType.toLowerCase()
  return (
    normalized.includes('int') ||
    normalized.includes('decimal') ||
    normalized.includes('double') ||
    normalized.includes('real') ||
    normalized.includes('float')
  )
}

function estimateColumnWidthPx(columnId: string, columnType?: string) {
  const contentChars = Math.max(columnId.length, columnType?.length ?? 0)
  const px = contentChars * 8 + 44
  return Math.min(340, Math.max(140, px))
}

function defaultFormatCellValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return value.toLocaleString()
  return String(value)
}

export function ObservatoryTable(props: ObservatoryTableProps) {
  return useObservatoryTable(props)
}

function useObservatoryTable({
  columns,
  columnTypes,
  rows,
  getRowId,
  onRowClick,
  monospace = false,
  maxHeightClassName = 'max-h-[420px]',
  containerClassName,
  enableSorting = true,
  enableColumnResizing = true,
  enableColumnPinning = true,
  formatCellValue = (value) => defaultFormatCellValue(value),
}: ObservatoryTableProps) {
  const { settings } = useObservatorySettings()
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnPinning, setColumnPinning] = useState<ColumnPinningState>({})

  const columnDefs = useMemo<Array<ColumnDef<ObservatoryRow>>>(() => {
    return columns.map((columnId, index) => ({
      accessorKey: columnId,
      enableSorting,
      size: estimateColumnWidthPx(columnId, columnTypes?.[index]),
      minSize: 120,
      maxSize: 520,
      header: () => {
        const typeLabel = columnTypes?.[index]
        const numeric = isNumericType(typeLabel)
        return (
          <div
            className={cn(
              'flex flex-col gap-0.5 min-w-0',
              numeric ? 'items-end' : '',
            )}
          >
            <span className="truncate">{columnId}</span>
            {typeLabel ? (
              <span className="text-xs font-normal text-muted-foreground truncate">
                {typeLabel}
              </span>
            ) : null}
          </div>
        )
      },
      cell: (ctx) => {
        const value = ctx.getValue<unknown>()
        const typeLabel = columnTypes?.[index]
        const numeric = isNumericType(typeLabel)
        const formatted = formatCellValue(value, columnId, typeLabel)
        return (
          <div
            className={cn('truncate', numeric ? 'text-right tabular-nums' : '')}
            title={formatted}
          >
            {formatted}
          </div>
        )
      },
    }))
  }, [columns, columnTypes, enableSorting, formatCellValue])

  const table = useReactTable({
    data: rows,
    columns: columnDefs,
    state: {
      sorting: enableSorting ? sorting : [],
      columnPinning: enableColumnPinning ? columnPinning : {},
    },
    onSortingChange: setSorting,
    onColumnPinningChange: setColumnPinning,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: enableSorting ? getSortedRowModel() : undefined,
    enableColumnResizing,
    columnResizeMode: 'onChange',
    getRowId: getRowId ? (row, index) => getRowId(row, index) : undefined,
  })

  const scrollContainerRef = useRef<HTMLDivElement | null>(null)
  const rowModel = table.getRowModel()
  const estimatedRowHeight = settings.ui.density === 'compact' ? 28 : 34
  const virtualizer = useVirtualizer({
    count: rowModel.rows.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => estimatedRowHeight,
    overscan: 8,
  })

  const virtualItems = virtualizer.getVirtualItems()
  const totalSize = virtualizer.getTotalSize()

  const headerGroup = table.getHeaderGroups()[0]

  return (
    <div
      className={cn(
        'flex flex-col rounded-none border border-border bg-card overflow-hidden text-xs',
        onRowClick ? 'select-none' : '',
        containerClassName,
      )}
    >
      <div
        ref={scrollContainerRef}
        className={cn(
          'relative w-full flex-1 overflow-auto min-h-0 touch-scroll',
          maxHeightClassName,
        )}
        role="table"
        aria-rowcount={rowModel.rows.length}
        aria-colcount={table.getAllLeafColumns().length}
      >
        <div style={{ width: table.getTotalSize() }}>
          <div
            className="sticky top-0 z-30 border-b border-border bg-muted/95"
            role="rowgroup"
          >
            <div className="flex" role="row">
              {headerGroup.headers.map((header) => {
                const column = header.column
                const isSorted = column.getIsSorted()
                const pinState = enableColumnPinning
                  ? column.getIsPinned()
                  : false

                const stickyStyles =
                  pinState === 'left'
                    ? {
                        position: 'sticky' as const,
                        left: column.getStart('left'),
                        zIndex: 30,
                      }
                    : pinState === 'right'
                      ? {
                          position: 'sticky' as const,
                          right: column.getAfter('right'),
                          zIndex: 30,
                        }
                      : undefined

                return (
                  <div
                    key={header.id}
                    className={cn(
                      'group flex items-stretch border-r border-border last:border-r-0',
                      pinState
                        ? 'bg-muted/50 shadow-[1px_0_0_0_var(--border)]'
                        : '',
                    )}
                    style={{ width: header.getSize(), ...stickyStyles }}
                    role="columnheader"
                    aria-sort={
                      isSorted === 'asc'
                        ? 'ascending'
                        : isSorted === 'desc'
                          ? 'descending'
                          : 'none'
                    }
                  >
                    <div className="flex-1 min-w-0 px-[var(--table-cell-px)] py-[var(--table-cell-py)]">
                      <div className="flex items-start justify-between gap-2">
                        <button
                          type="button"
                          className={cn(
                            'min-w-0 flex-1 text-left font-medium leading-tight',
                            enableSorting
                              ? 'hover:bg-muted/50 transition-colors'
                              : '',
                          )}
                          onClick={
                            enableSorting
                              ? column.getToggleSortingHandler()
                              : undefined
                          }
                          title={String(column.id)}
                        >
                          {header.isPlaceholder
                            ? null
                            : flexRender(
                                header.column.columnDef.header,
                                header.getContext(),
                              )}
                        </button>

                        <div className="flex items-center gap-1 text-muted-foreground">
                          {isSorted === 'asc' ? (
                            <ArrowUp className="size-3.5" />
                          ) : isSorted === 'desc' ? (
                            <ArrowDown className="size-3.5" />
                          ) : null}
                          {enableColumnPinning ? (
                            <DropdownMenu>
                              <DropdownMenuTrigger
                                className={cn(
                                  'inline-flex size-7 items-center justify-center hover:bg-muted/60',
                                  'opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity',
                                )}
                                aria-label={`Column actions for ${String(column.id)}`}
                              >
                                <MoreVertical className="size-4" />
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                {pinState ? (
                                  <DropdownMenuItem
                                    onClick={() => column.pin(false)}
                                  >
                                    <PinOff className="size-4" />
                                    Unpin
                                  </DropdownMenuItem>
                                ) : (
                                  <DropdownMenuItem
                                    onClick={() => column.pin('left')}
                                  >
                                    <Pin className="size-4" />
                                    Pin left
                                  </DropdownMenuItem>
                                )}
                              </DropdownMenuContent>
                            </DropdownMenu>
                          ) : null}
                        </div>
                      </div>
                    </div>

                    {enableColumnResizing && column.getCanResize() ? (
                      <div
                        onMouseDown={header.getResizeHandler()}
                        onTouchStart={header.getResizeHandler()}
                        className={cn(
                          'w-1 cursor-col-resize select-none touch-none',
                          'bg-transparent hover:bg-primary/25',
                          column.getIsResizing() ? 'bg-primary/40' : '',
                        )}
                        aria-hidden="true"
                      />
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>

          <div
            className="relative"
            role="rowgroup"
            style={{ height: totalSize }}
          >
            {virtualItems.map((virtualRow) => {
              const row = rowModel.rows[virtualRow.index]
              return (
                <div
                  key={row.id}
                  className={cn(
                    'absolute left-0 right-0 flex border-b border-border last:border-b-0 bg-card',
                    onRowClick
                      ? 'hover:bg-muted/30 cursor-pointer transition-colors'
                      : '',
                  )}
                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                  role="row"
                  tabIndex={onRowClick ? 0 : undefined}
                  onClick={
                    onRowClick ? () => onRowClick(row.original) : undefined
                  }
                  onKeyDown={
                    onRowClick
                      ? (event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            onRowClick(row.original)
                          }
                        }
                      : undefined
                  }
                >
                  {row.getVisibleCells().map((cell) => {
                    const column = cell.column
                    const pinState = enableColumnPinning
                      ? column.getIsPinned()
                      : false

                    const stickyStyles =
                      pinState === 'left'
                        ? {
                            position: 'sticky' as const,
                            left: column.getStart('left'),
                            zIndex: 10,
                          }
                        : pinState === 'right'
                          ? {
                              position: 'sticky' as const,
                              right: column.getAfter('right'),
                              zIndex: 10,
                            }
                          : undefined

                    return (
                      <div
                        key={cell.id}
                        className={cn(
                          'px-[var(--table-cell-px)] py-[var(--table-cell-py)] text-foreground border-r border-border last:border-r-0',
                          'min-w-0 whitespace-nowrap align-middle',
                          monospace ? 'font-mono text-xs' : '',
                          pinState
                            ? 'bg-card shadow-[1px_0_0_0_var(--border)]'
                            : '',
                        )}
                        style={{ width: column.getSize(), ...stickyStyles }}
                        role="cell"
                        title={String(cell.getValue() ?? '')}
                      >
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
