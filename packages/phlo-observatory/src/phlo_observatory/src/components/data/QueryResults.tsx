/**
 * Renders a DataPreviewResult as an ObservatoryTable with a row-count summary
 * and CSV download. Row clicks hand the row and column types to onShowJourney
 * when provided.
 */
import { Download } from 'lucide-react'
import type { DataPreviewResult, DataRow } from '@/observatory/api/trino'
import { Button } from '@/components/ui/button'
import { ObservatoryTable } from '@/components/data/ObservatoryTable'

interface QueryResultsProps {
  results: DataPreviewResult
  onShowJourney?: (
    rowData: Record<string, unknown>,
    columnTypes: Array<string>,
  ) => void
}

export function QueryResults({ results, onShowJourney }: QueryResultsProps) {
  const handleRowClick = (row: DataRow) => {
    if (onShowJourney) {
      onShowJourney(row, results.columnTypes)
    }
  }

  const downloadCSV = () => {
    const header = results.columns.join(',')
    const rows = results.rows.map((row) =>
      results.columns
        .map((col) => {
          const val = row[col]
          if (val === null || val === undefined) return ''
          if (typeof val === 'string' && val.includes(',')) {
            return `"${val.replace(/"/g, '""')}"`
          }
          return String(val)
        })
        .join(','),
    )
    const csv = [header, ...rows].join('\n')

    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'query_results.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b">
        <div className="text-sm text-muted-foreground">
          {results.rows.length} row{results.rows.length !== 1 ? 's' : ''}
          {results.hasMore && '+'} • {results.columns.length} columns
        </div>
        <Button variant="outline" size="xs" onClick={downloadCSV}>
          <Download className="size-3" />
          CSV
        </Button>
      </div>

      {/* Table */}
      <div className="flex-1 p-3">
        <ObservatoryTable
          columns={results.columns}
          columnTypes={results.columnTypes}
          rows={results.rows}
          getRowId={(_, index) => String(index)}
          onRowClick={
            onShowJourney ? (row) => handleRowClick(row as DataRow) : undefined
          }
          containerClassName="h-full"
          maxHeightClassName="h-full"
          enableSorting
          enableColumnResizing
          enableColumnPinning
          monospace
          formatCellValue={(value) =>
            formatValue(value as DataRow[keyof DataRow])
          }
        />
      </div>
    </div>
  )
}

function formatValue(value: DataRow[keyof DataRow]): string {
  if (value === null || value === undefined) {
    return '—'
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false'
  }
  if (typeof value === 'number') {
    return value.toLocaleString()
  }
  return String(value)
}
