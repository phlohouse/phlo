/**
 * Stage Diff Component
 *
 * Visual diff view comparing data between pipeline stages.
 * Shows column changes, transformations, and aggregation explanations.
 */

import { useCallback, useEffect, useState } from 'react'

import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Columns,
  Loader2,
  Minus,
  Plus,
  RefreshCw,
} from 'lucide-react'

import type { ColumnDiff, StageDiffResult } from '@/observatory/api/diff'
import type { TransformType } from '@/utils/sqlParser'

import { Badge } from '@/components/ui/badge'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { getSimpleStageDiff, getStageDiff } from '@/observatory/api/diff'

interface StageDiffProps {
  open: boolean
  onClose: () => void
  upstreamAssetKey: string
  downstreamAssetKey: string
  transformationSql?: string
  upstreamColumns: Array<string>
  downstreamColumns: Array<string>
}

/**
 * Badge color and label for transform types
 */
function getTransformTypeBadge(transformType: TransformType): {
  label: string
  variant: 'default' | 'secondary' | 'destructive' | 'outline'
  className?: string
} {
  switch (transformType) {
    case 'ONE_TO_ONE':
      return {
        label: '1:1',
        variant: 'outline',
        className: 'border-green-500 text-green-500',
      }
    case 'ONE_TO_MANY':
      return {
        label: '1:N',
        variant: 'outline',
        className: 'border-blue-500 text-blue-500',
      }
    case 'MANY_TO_ONE':
      return {
        label: 'N:1',
        variant: 'outline',
        className: 'border-amber-500 text-amber-500',
      }
    case 'COMPLEX':
      return {
        label: 'Complex',
        variant: 'outline',
        className: 'border-red-500 text-red-500',
      }
  }
}

/**
 * Icon and color for column change types
 */
function getColumnChangeDisplay(changeType: ColumnDiff['changeType']): {
  icon: typeof Plus
  className: string
  label: string
} {
  switch (changeType) {
    case 'added':
      return {
        icon: Plus,
        className: 'text-green-500',
        label: 'Added',
      }
    case 'removed':
      return {
        icon: Minus,
        className: 'text-red-500',
        label: 'Removed',
      }
    case 'renamed':
      return {
        icon: ArrowRight,
        className: 'text-amber-500',
        label: 'Renamed',
      }
    case 'transformed':
      return {
        icon: RefreshCw,
        className: 'text-blue-500',
        label: 'Transformed',
      }
    case 'unchanged':
      return {
        icon: Columns,
        className: 'text-muted-foreground',
        label: 'Unchanged',
      }
  }
}

/**
 * Column diff list grouped by change type
 */
function ColumnDiffList({
  diffs,
  changeType,
  defaultExpanded = true,
}: {
  diffs: Array<ColumnDiff>
  changeType: ColumnDiff['changeType']
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(() => defaultExpanded)
  const filteredDiffs = diffs.filter((d) => d.changeType === changeType)
  const display = getColumnChangeDisplay(changeType)
  const Icon = display.icon

  if (filteredDiffs.length === 0) return null

  return (
    <div className="border border-border rounded">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-2 py-1.5 bg-muted/30 hover:bg-muted/50"
      >
        <Icon className={cn('size-3.5', display.className)} />
        <span className={cn('text-xs font-medium', display.className)}>
          {display.label}
        </span>
        <span className="text-xs text-muted-foreground">
          {filteredDiffs.length}
        </span>
        <div className="flex-1" />
        {expanded ? (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" />
        )}
      </button>

      {expanded && (
        <div className="px-2 py-1.5 flex flex-wrap gap-1 border-t border-border bg-background">
          {filteredDiffs.map((diff) => (
            <code
              key={`${diff.column}-${diff.changeType}-${diff.sourceColumn ?? ''}`}
              className={cn(
                'text-xs px-1.5 py-0.5 rounded bg-muted',
                changeType === 'removed' && 'text-red-500 line-through',
                changeType === 'added' && 'text-green-500',
                changeType === 'renamed' && 'text-amber-500',
                changeType === 'transformed' && 'text-blue-500',
                changeType === 'unchanged' && 'text-muted-foreground',
              )}
              title={
                diff.transformation ? `${diff.transformation}()` : undefined
              }
            >
              {diff.column}
            </code>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Aggregation explanation panel
 */
function AggregationExplanation({
  aggregation,
}: {
  aggregation: NonNullable<StageDiffResult['aggregation']>
}) {
  return (
    <div className="border border-amber-500/30 bg-amber-500/5 rounded-none p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Badge variant="outline" className="border-amber-500 text-amber-500">
          N:1
        </Badge>
        <span className="text-sm font-medium text-amber-500">
          Aggregation Transform
        </span>
      </div>

      {aggregation.groupBy.length > 0 && (
        <div>
          <div className="text-xs text-muted-foreground mb-1">Grouped by</div>
          <div className="flex flex-wrap gap-1">
            {aggregation.groupBy.map((col) => (
              <code
                key={col}
                className="text-xs bg-muted px-1.5 py-0.5 rounded"
              >
                {col}
              </code>
            ))}
          </div>
        </div>
      )}

      {aggregation.aggregates.length > 0 && (
        <div>
          <div className="text-xs text-muted-foreground mb-1">Aggregations</div>
          <div className="space-y-1">
            {aggregation.aggregates.map((agg) => (
              <div
                key={`${agg.expression}->${agg.alias}`}
                className="flex items-center gap-2 text-xs"
              >
                <code className="bg-muted px-1.5 py-0.5 rounded text-muted-foreground">
                  {agg.expression}
                </code>
                <ArrowRight className="size-3 text-muted-foreground" />
                <code className="bg-muted px-1.5 py-0.5 rounded text-primary">
                  {agg.alias}
                </code>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="text-xs text-muted-foreground">
        Multiple source rows are combined into single downstream rows via GROUP
        BY.
      </div>
    </div>
  )
}

/**
 * Main Stage Diff component
 */
export function StageDiff({
  open,
  onClose,
  upstreamAssetKey,
  downstreamAssetKey,
  transformationSql,
  upstreamColumns,
  downstreamColumns,
}: StageDiffProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [diff, setDiff] = useState<StageDiffResult | null>(null)

  const upstreamName = upstreamAssetKey.split('/').pop() || upstreamAssetKey
  const downstreamName =
    downstreamAssetKey.split('/').pop() || downstreamAssetKey

  const loadDiff = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      let result: StageDiffResult

      if (transformationSql) {
        result = await getStageDiff({
          data: {
            transformationSql,
            upstreamColumns,
            downstreamColumns,
          },
        })
      } else {
        result = await getSimpleStageDiff({
          data: {
            upstreamColumns,
            downstreamColumns,
          },
        })
      }

      setDiff(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to compute diff')
    } finally {
      setLoading(false)
    }
  }, [transformationSql, upstreamColumns, downstreamColumns])

  useEffect(() => {
    if (!open) return
    void loadDiff()
  }, [loadDiff, open])

  return (
    <Sheet open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <SheetContent
        side="bottom"
        className="h-[60vh] sm:h-[50vh] sm:max-w-none flex flex-col"
      >
        <SheetHeader className="space-y-1">
          <SheetTitle className="text-sm font-semibold">Stage Diff</SheetTitle>
          <SheetDescription className="text-xs">
            <code className="bg-muted px-1.5 py-0.5 rounded">
              {upstreamName}
            </code>
            {' → '}
            <code className="bg-muted px-1.5 py-0.5 rounded text-primary">
              {downstreamName}
            </code>
          </SheetDescription>
        </SheetHeader>

        <div className="mt-4 px-6 py-4 space-y-3 overflow-y-auto flex-1">
          {loading && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              <span className="text-xs">Computing diff…</span>
            </div>
          )}

          {error && (
            <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded p-2">
              {error}
            </div>
          )}

          {diff && !loading && (
            <>
              {/* Transform type and confidence */}
              <div className="flex items-center gap-3 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">Transform:</span>
                  <Badge
                    variant={getTransformTypeBadge(diff.transformType).variant}
                    className={cn(
                      'text-[10px] px-1.5 py-0',
                      getTransformTypeBadge(diff.transformType).className,
                    )}
                  >
                    {getTransformTypeBadge(diff.transformType).label}
                  </Badge>
                </div>
                <span className="text-muted-foreground">
                  Confidence:{' '}
                  <span
                    className={
                      diff.confidence >= 50
                        ? 'text-green-500'
                        : 'text-amber-500'
                    }
                  >
                    {diff.confidence}%
                  </span>
                </span>
              </div>

              {/* Summary */}
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                {diff.summary.addedCount > 0 && (
                  <span className="text-green-500">
                    +{diff.summary.addedCount} added
                  </span>
                )}
                {diff.summary.removedCount > 0 && (
                  <span className="text-red-500">
                    -{diff.summary.removedCount} removed
                  </span>
                )}
                {diff.summary.renamedCount > 0 && (
                  <span className="text-amber-500">
                    ~{diff.summary.renamedCount} renamed
                  </span>
                )}
                {diff.summary.transformedCount > 0 && (
                  <span className="text-blue-500">
                    ⟳{diff.summary.transformedCount} transformed
                  </span>
                )}
                {diff.summary.unchangedCount > 0 && (
                  <span className="text-muted-foreground">
                    ={diff.summary.unchangedCount} unchanged
                  </span>
                )}
              </div>

              {/* Aggregation explanation */}
              {diff.aggregation && (
                <AggregationExplanation aggregation={diff.aggregation} />
              )}

              {/* Column diffs */}
              <div className="space-y-2">
                <h4 className="text-xs font-medium text-foreground">
                  Column Changes
                </h4>
                <ColumnDiffList
                  diffs={diff.columnDiffs}
                  changeType="added"
                  defaultExpanded={true}
                />
                <ColumnDiffList
                  diffs={diff.columnDiffs}
                  changeType="removed"
                  defaultExpanded={true}
                />
                <ColumnDiffList
                  diffs={diff.columnDiffs}
                  changeType="renamed"
                  defaultExpanded={true}
                />
                <ColumnDiffList
                  diffs={diff.columnDiffs}
                  changeType="transformed"
                  defaultExpanded={true}
                />
                <ColumnDiffList
                  diffs={diff.columnDiffs}
                  changeType="unchanged"
                  defaultExpanded={false}
                />
              </div>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
