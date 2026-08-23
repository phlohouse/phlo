/**
 * React Flow trace of one row's journey: places the row's source stage at the
 * center with upstream and downstream assets around it, and shows per-node
 * detail (compiled SQL, quality checks, stage rows) beneath the canvas.
 */
import { useCallback, useEffect, useMemo, useReducer, useState } from 'react'

import { Background, Controls, MarkerType, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  AlertCircle,
  CheckCircle,
  Code,
  Database,
  GitCompare,
  Loader2,
  Terminal,
} from 'lucide-react'
import { Highlight, themes } from 'prism-react-renderer'

import type { ContributingRowsPageResult } from '@/observatory/api/contributing'
import type { DataRow } from '@/observatory/api/trino'
import type { Edge, Node } from '@xyflow/react'

import type { JourneyNodeData } from '@/components/flow/nodeTypes'
import { journeyNodeTypes } from '@/components/flow/nodeTypes'
import { ObservatoryTable } from '@/components/data/ObservatoryTable'
import { StageDiff } from '@/components/data/StageDiff'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { useObservatorySettings } from '@/hooks/useObservatorySettings'
import {
  getContributingRowsPage,
  getContributingRowsQuery,
} from '@/observatory/api/contributing'
import { getAssetDetails } from '@/observatory/api/dagster'
import { getAssetNeighbors } from '@/observatory/api/graph'
import { getAssetChecks } from '@/observatory/api/quality'

interface RowJourneyProps {
  assetKey: string
  rowData: Record<string, unknown>
  columnTypes: Array<string>
  className?: string
  onQuerySource?: (query: string) => void
}

interface NodeDetails {
  sql?: string
  checks?: Array<{ name: string; status: string }>
  stageData?: Array<DataRow>
  upstreamAssetKeys?: Array<string>
  upstreamColumns?: Record<string, Array<string>> // assetKey -> columns
}

function extractTransformationSql(asset: {
  description?: string
  metadata?: Array<{ key: string; value: string }>
}): string | undefined {
  // First, look for SQL in metadata (e.g., phlo/compiled_sql from dbt translator)
  const candidates =
    asset.metadata
      ?.filter((m) => m.value && m.value.trim() && /sql/i.test(m.key))
      .sort((a, b) => b.value.length - a.value.length) ?? []
  const metadataSql = candidates[0]?.value?.trim()
  if (metadataSql) return metadataSql

  // Fall back to description only if it looks like actual SQL
  const desc = asset.description?.trim()
  if (
    desc &&
    /^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|ALTER)/i.test(desc)
  ) {
    return desc
  }

  return undefined
}

// Detail panel component shown below the flow
type NodeDetailPanelProps = {
  assetKey: string
  isLoading: boolean
  details: NodeDetails | null
  rowData: Record<string, unknown>
  onQuerySource?: (query: string) => void
}

function NodeDetailPanel(props: NodeDetailPanelProps) {
  return useNodeDetailPanel(props)
}

function useNodeDetailPanel({
  assetKey,
  isLoading,
  details,
  rowData,
  onQuerySource,
}: NodeDetailPanelProps) {
  const { settings } = useObservatorySettings()
  const tableName = assetKey.split('/').pop() || assetKey
  const [contribOpen, setContribOpen] = useState(false)
  const [contribUpstreamAssetKey, setContribUpstreamAssetKey] = useState<
    string | null
  >(null)
  const [contribPage, setContribPage] = useState(0)
  const [contribPageSize, setContribPageSize] = useState(50)
  const [contribLoading, setContribLoading] = useState(false)
  const [contribError, setContribError] = useState<string | null>(null)
  const [contribResult, setContribResult] = useState<Exclude<
    ContributingRowsPageResult,
    { error: string }
  > | null>(null)

  // Stage diff state
  const [diffOpen, setDiffOpen] = useState(false)
  const [diffUpstreamAssetKey, setDiffUpstreamAssetKey] = useState<
    string | null
  >(null)

  const loadContributingRows = useCallback(
    async (upstreamAssetKey: string) => {
      setContribLoading(true)
      setContribError(null)
      try {
        const result = await getContributingRowsPage({
          data: {
            downstreamAssetKey: assetKey,
            upstreamAssetKey,
            rowData,
            page: contribPage,
            pageSize: contribPageSize,
            trinoUrl: settings.connections.trinoUrl,
            timeoutMs: settings.query.timeoutMs,
            catalog: settings.defaults.catalog,
          },
        })

        if ('error' in result) {
          setContribError(result.error)
          setContribResult(null)
          return
        }

        setContribResult(result)
      } catch (err) {
        setContribError(
          err instanceof Error
            ? err.message
            : 'Failed to load contributing rows',
        )
        setContribResult(null)
      } finally {
        setContribLoading(false)
      }
    },
    [
      assetKey,
      contribPage,
      contribPageSize,
      rowData,
      settings.connections.trinoUrl,
      settings.defaults.catalog,
      settings.query.timeoutMs,
    ],
  )

  useEffect(() => {
    if (!contribOpen) return
    if (!contribUpstreamAssetKey) return
    void loadContributingRows(contribUpstreamAssetKey)
  }, [contribOpen, contribUpstreamAssetKey, loadContributingRows])

  if (isLoading) {
    return (
      <div className="bg-card border border-border p-6">
        <div className="flex items-center gap-3">
          <Loader2 className="size-5 text-primary animate-spin" />
          <span className="text-muted-foreground">
            Loading details for {tableName}…
          </span>
        </div>
      </div>
    )
  }

  if (!details) {
    return (
      <div className="bg-card border border-border p-6 text-center text-muted-foreground">
        <Database className="size-8 mx-auto mb-2 opacity-50" />
        <p>Click a node above to view its details</p>
      </div>
    )
  }

  // Simple data row count message
  const getDataRowMessage = () => {
    const count = details.stageData?.length || 0
    const countLabel = `(${count} row${count !== 1 ? 's' : ''})`
    return { title: 'Source Data from', subtitle: countLabel }
  }

  return (
    <div className="bg-card border border-border overflow-hidden">
      <Sheet
        open={contribOpen}
        onOpenChange={(open) => {
          setContribOpen(open)
          if (!open) {
            setContribUpstreamAssetKey(null)
            setContribError(null)
            setContribResult(null)
            setContribPage(0)
          }
        }}
      >
        <SheetContent
          side="bottom"
          className="h-[85vh] sm:h-[75vh] sm:max-w-none"
        >
          <SheetHeader className="flex-row items-center justify-between gap-4 space-y-0">
            <div className="flex flex-col gap-0.5">
              <SheetTitle>Contributing rows</SheetTitle>
              <SheetDescription className="text-xs">
                {contribResult?.upstream
                  ? `${contribResult.upstream.schema}.${contribResult.upstream.table}`
                  : contribUpstreamAssetKey
                    ? contribUpstreamAssetKey.split('/').pop()
                    : ''}
              </SheetDescription>
            </div>

            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <Label
                  htmlFor="contrib-page-size"
                  className="text-xs text-muted-foreground whitespace-nowrap"
                >
                  Page size
                </Label>
                <select
                  id="contrib-page-size"
                  className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                  value={String(contribPageSize)}
                  onChange={(e) => {
                    setContribPageSize(Number(e.target.value))
                    setContribPage(0)
                  }}
                >
                  <option value="25">25</option>
                  <option value="50">50</option>
                  <option value="100">100</option>
                  <option value="200">200</option>
                </select>
              </div>

              {contribResult?.mode ? (
                <Badge variant="secondary" className="capitalize">
                  {contribResult.mode}
                </Badge>
              ) : null}

              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!contribResult?.query || !onQuerySource}
                onClick={() => {
                  if (!contribResult?.query || !onQuerySource) return
                  onQuerySource(contribResult.query)
                }}
              >
                Open in SQL
              </Button>

              <div className="h-4 w-px bg-border" />

              <div className="text-xs text-muted-foreground whitespace-nowrap">
                Page {contribPage + 1}
                {contribResult
                  ? ` • ${contribResult.rows.length} row${
                      contribResult.rows.length === 1 ? '' : 's'
                    }`
                  : ''}
              </div>

              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={contribPage === 0 || contribLoading}
                onClick={() => setContribPage((p) => Math.max(0, p - 1))}
              >
                Prev
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!contribResult?.hasMore || contribLoading}
                onClick={() => setContribPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </SheetHeader>

          <div className="px-4 pb-4 flex flex-col gap-4 min-h-0 flex-1">
            {contribResult?.query ? (
              <details className="rounded-md border border-border bg-muted/30">
                <summary className="cursor-pointer select-none px-3 py-2 text-xs text-muted-foreground">
                  SQL (read-only)
                </summary>
                <div className="border-t border-border overflow-x-auto max-h-40">
                  <pre className="p-3 text-xs leading-relaxed">
                    {cleanSqlForDisplay(contribResult.query)}
                  </pre>
                </div>
              </details>
            ) : null}

            <div className="min-h-0 flex-1">
              {contribLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Loading…
                </div>
              ) : contribError ? (
                <div className="text-sm text-red-400">{contribError}</div>
              ) : contribResult && contribResult.rows.length > 0 ? (
                <ObservatoryTable
                  columns={contribResult.columns}
                  rows={contribResult.rows}
                  getRowId={(_, index) => String(index)}
                  maxHeightClassName="max-h-full"
                  enableSorting
                  enableColumnResizing
                  enableColumnPinning
                  monospace
                />
              ) : (
                <div className="text-sm text-muted-foreground">
                  No contributing rows found.
                </div>
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>

      {/* Stage Diff Sheet */}
      {diffUpstreamAssetKey && (
        <StageDiff
          open={diffOpen}
          onClose={() => {
            setDiffOpen(false)
            setDiffUpstreamAssetKey(null)
          }}
          upstreamAssetKey={diffUpstreamAssetKey}
          downstreamAssetKey={assetKey}
          transformationSql={details?.sql}
          upstreamColumns={
            details?.upstreamColumns?.[diffUpstreamAssetKey] ?? []
          }
          downstreamColumns={Object.keys(rowData)}
        />
      )}

      <div className="p-4 border-b border-border flex items-center justify-between">
        <h4 className="font-medium text-foreground flex items-center gap-2">
          <Database className="size-4 text-primary" />
          {tableName}
        </h4>
      </div>

      <div className="p-4 space-y-4">
        {/* Transformation SQL */}
        {details.sql && (
          <div>
            <div className="flex items-center gap-2 text-sm text-foreground mb-2 font-medium">
              <Code className="size-4" />
              Transformation SQL
            </div>
            <Highlight
              theme={themes.vsDark}
              code={cleanSqlForDisplay(details.sql)}
              language="sql"
            >
              {({ style, tokens, getLineProps, getTokenProps }) => (
                <div className="rounded-md border border-border bg-muted/30 overflow-x-auto max-h-64">
                  <pre
                    style={{
                      ...style,
                      margin: 0,
                      backgroundColor: 'transparent',
                    }}
                    className="p-3 text-xs leading-relaxed"
                  >
                    {tokens.map((line, lineIndex) => (
                      <div
                        key={`${lineIndex}:${line
                          .map(
                            (token) =>
                              `${token.content}:${token.types.join('.')}`,
                          )
                          .join('|')}`}
                        {...getLineProps({ line })}
                      >
                        {line.map((token, tokenIndex) => (
                          <span
                            key={`${lineIndex}:${tokenIndex}:${token.content}:${token.types.join('.')}`}
                            {...getTokenProps({ token })}
                          />
                        ))}
                      </div>
                    ))}
                  </pre>
                </div>
              )}
            </Highlight>
          </div>
        )}

        {/* Contributing rows (aggregates) / upstream lookup (1:1) */}
        {onQuerySource &&
          details.upstreamAssetKeys &&
          details.upstreamAssetKeys.length > 0 && (
            <div>
              <div className="flex items-center gap-2 text-sm text-foreground mb-2 font-medium">
                <Terminal className="size-4" />
                Contributing rows
              </div>
              <div className="space-y-2">
                {details.upstreamAssetKeys.map((upstreamAssetKey) => {
                  const upstreamLabel = upstreamAssetKey.split('/').pop()
                  return (
                    <div
                      key={upstreamAssetKey}
                      className="flex items-center justify-between gap-3 rounded-md border border-border bg-muted/20 px-3 py-2"
                    >
                      <div className="text-xs text-foreground">
                        {upstreamLabel}
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-7 text-xs"
                          onClick={() => {
                            setDiffUpstreamAssetKey(upstreamAssetKey)
                            setDiffOpen(true)
                          }}
                        >
                          <GitCompare className="size-3 mr-1" />
                          Compare
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-7 text-xs"
                          onClick={() => {
                            setContribUpstreamAssetKey(upstreamAssetKey)
                            setContribOpen(true)
                            setContribPage(0)
                          }}
                        >
                          View rows
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-7 text-xs"
                          onClick={async () => {
                            const result = await getContributingRowsQuery({
                              data: {
                                downstreamAssetKey: assetKey,
                                upstreamAssetKey,
                                rowData,
                                limit: 100,
                                trinoUrl: settings.connections.trinoUrl,
                                timeoutMs: settings.query.timeoutMs,
                                catalog: settings.defaults.catalog,
                              },
                            })

                            if ('error' in result) {
                              console.error(
                                '[ContributingRows] Error:',
                                result.error,
                              )
                              return
                            }

                            onQuerySource(result.query)
                          }}
                        >
                          Open SQL
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

        {/* Quality Checks */}
        {details.checks && details.checks.length > 0 && (
          <div>
            <div className="flex items-center gap-2 text-sm text-foreground mb-2 font-medium">
              <CheckCircle className="size-4" />
              Quality Checks
            </div>
            <div className="flex flex-wrap gap-2">
              {details.checks.map((check) => (
                <div
                  key={check.name}
                  className="flex items-center gap-2 text-xs bg-muted/30 border border-border px-3 py-1.5 rounded"
                >
                  {check.status === 'PASSED' ? (
                    <CheckCircle className="size-4 text-green-400 flex-shrink-0" />
                  ) : (
                    <AlertCircle className="size-4 text-red-400 flex-shrink-0" />
                  )}
                  <span className="text-foreground">{check.name}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Row Data at this Stage */}
        {details.stageData && details.stageData.length > 0 && (
          <div>
            <div className="flex items-center gap-2 text-sm text-foreground mb-2 font-medium">
              <Database className="size-4" />
              {getDataRowMessage().title}{' '}
              <code className="bg-muted px-1.5 py-0.5 rounded text-primary ml-1">
                {tableName}
              </code>
              <span className="text-muted-foreground">
                {getDataRowMessage().subtitle}
              </span>
            </div>
            <ObservatoryTable
              columns={Object.keys(details.stageData[0])}
              rows={details.stageData}
              getRowId={(_, index) => String(index)}
              maxHeightClassName="max-h-64"
              enableSorting
              enableColumnResizing
              enableColumnPinning
              monospace
            />
          </div>
        )}

        {/* No data found */}
        {!details.sql &&
          (!details.checks || details.checks.length === 0) &&
          (!details.stageData || details.stageData.length === 0) && (
            <div className="text-sm text-muted-foreground">
              No additional details available for this asset.
            </div>
          )}
      </div>
    </div>
  )
}

// Clean SQL string for display by removing common prefixes
function cleanSqlForDisplay(sql: string): string {
  // Remove dbt model header, Raw SQL section, and markdown code fences
  const cleaned = sql
    // Remove "dbt model xxx" line at the start
    .replace(/^dbt\s+model\s+\S+\s*/i, '')
    // Remove "#### Raw SQL:" or similar headers
    .replace(/^#+\s*(Raw\s+)?SQL:\s*/im, '')
    // Remove markdown code fences
    .replace(/^```sql\s*/im, '')
    .replace(/```\s*$/im, '')
    // Also try a combined pattern for the full header block
    .replace(/^dbt\s+model\s+\S+[\s\S]*?```sql\s*/i, '')
    .trim()
  return cleaned
}

export function RowJourney(props: RowJourneyProps) {
  return useRowJourney(props)
}

function useRowJourney({
  assetKey,
  rowData,
  className = '',
  onQuerySource,
}: RowJourneyProps) {
  type GraphData = {
    nodes: Array<{ keyPath: string; label: string; computeKind?: string }>
    edges: Array<{ source: string; target: string }>
  }
  type JourneyState = {
    detailsLoading: boolean
    error: string | null
    graphData: GraphData | null
    loading: boolean
    nodeDetails: NodeDetails | null
    selectedNode: string | null
  }
  type JourneyAction =
    | { type: 'graphLoading' }
    | { type: 'graphError'; error: string }
    | { type: 'graphLoaded'; assetKey: string; graphData: GraphData }
    | { type: 'nodeSelected'; selectedNode: string }
    | { type: 'detailsLoading' }
    | { type: 'detailsLoaded'; nodeDetails: NodeDetails | null }
  const [state, dispatch] = useReducer(
    (current: JourneyState, action: JourneyAction): JourneyState => {
      switch (action.type) {
        case 'graphLoading':
          return { ...current, error: null, loading: true }
        case 'graphError':
          return { ...current, error: action.error, loading: false }
        case 'graphLoaded':
          return {
            ...current,
            error: null,
            graphData: action.graphData,
            loading: false,
            selectedNode: action.assetKey,
          }
        case 'nodeSelected':
          return { ...current, selectedNode: action.selectedNode }
        case 'detailsLoading':
          return { ...current, detailsLoading: true }
        case 'detailsLoaded':
          return {
            ...current,
            detailsLoading: false,
            nodeDetails: action.nodeDetails,
          }
      }
    },
    {
      detailsLoading: false,
      error: null,
      graphData: null,
      loading: true,
      nodeDetails: null,
      selectedNode: null,
    },
  )
  const {
    detailsLoading,
    error,
    graphData,
    loading,
    nodeDetails,
    selectedNode,
  } = state
  const { settings } = useObservatorySettings()
  // Load asset neighbors
  useEffect(() => {
    let cancelled = false

    async function loadGraph() {
      dispatch({ type: 'graphLoading' })
      try {
        const result = await getAssetNeighbors({
          data: {
            assetKey,
            direction: 'both',
            depth: 2,
          },
        })
        if (cancelled) return
        if ('error' in result) {
          dispatch({ type: 'graphError', error: result.error })
        } else {
          dispatch({ type: 'graphLoaded', assetKey, graphData: result })
        }
      } catch (err) {
        if (cancelled) return
        dispatch({
          type: 'graphError',
          error: err instanceof Error ? err.message : 'Failed to load lineage',
        })
      }
    }
    void loadGraph()
    return () => {
      cancelled = true
    }
  }, [assetKey])

  // Load details when node is selected
  useEffect(() => {
    const nodeKey = selectedNode
    if (!nodeKey) {
      dispatch({ type: 'detailsLoaded', nodeDetails: null })
      return
    }

    let cancelled = false
    const nodeAssetKey = nodeKey.split('/')
    async function loadNodeDetails() {
      dispatch({ type: 'detailsLoading' })
      try {
        // Fetch asset details and quality checks
        const [assetInfo, qualityInfo] = await Promise.all([
          getAssetDetails({
            data: {
              assetKey: nodeAssetKey,
              dagsterUrl: settings.connections.dagsterGraphqlUrl,
            },
          }),
          getAssetChecks({
            data: {
              assetKey: nodeAssetKey,
              dagsterUrl: settings.connections.dagsterGraphqlUrl,
            },
          }),
        ])

        if (cancelled) return
        const checks =
          'error' in qualityInfo
            ? []
            : qualityInfo.map((check) => ({
                name: check.name,
                status: check.status,
              }))

        const sql =
          'error' in assetInfo ? undefined : extractTransformationSql(assetInfo)

        const upstreamAssetKeys: Array<string> = []
        if (graphData) {
          for (const edge of graphData.edges) {
            if (edge.target === nodeKey) {
              upstreamAssetKeys.push(edge.source)
            }
          }
        }

        dispatch({
          type: 'detailsLoaded',
          nodeDetails: {
            sql,
            checks,
            stageData: undefined,
            upstreamAssetKeys,
          },
        })
      } catch (err) {
        if (cancelled) return
        console.error('Failed to load node details:', err)
        dispatch({ type: 'detailsLoaded', nodeDetails: null })
      }
    }

    void loadNodeDetails()
    return () => {
      cancelled = true
    }
  }, [selectedNode, rowData, graphData, settings.connections.dagsterGraphqlUrl])

  // Handle node selection
  const handleNodeSelect = useCallback((nodeKey: string) => {
    dispatch({ type: 'nodeSelected', selectedNode: nodeKey })
  }, [])

  // Convert graph data to React Flow format
  const { nodes, edges } = useMemo(() => {
    if (!graphData) return { nodes: [], edges: [] }

    // Calculate horizontal layout based on depth
    const currentNode = graphData.nodes.find((n) => n.keyPath === assetKey)
    if (!currentNode) return { nodes: [], edges: [] }

    // BFS to find depths
    const depths = new Map<string, number>()
    depths.set(assetKey, 0)

    // Find upstream nodes
    const upstreamQueue = [assetKey]
    while (upstreamQueue.length > 0) {
      const current = upstreamQueue.shift()!
      const currentDepth = depths.get(current)!

      for (const edge of graphData.edges) {
        if (edge.target === current && !depths.has(edge.source)) {
          depths.set(edge.source, currentDepth - 1)
          upstreamQueue.push(edge.source)
        }
      }
    }

    // Find downstream nodes
    const downstreamQueue = [assetKey]
    while (downstreamQueue.length > 0) {
      const current = downstreamQueue.shift()!
      const currentDepth = depths.get(current)!

      for (const edge of graphData.edges) {
        if (edge.source === current && !depths.has(edge.target)) {
          depths.set(edge.target, currentDepth + 1)
          downstreamQueue.push(edge.target)
        }
      }
    }

    // Group nodes by depth
    const nodesByDepth = new Map<number, typeof graphData.nodes>()
    for (const node of graphData.nodes) {
      const depth = depths.get(node.keyPath) ?? 0
      if (!nodesByDepth.has(depth)) {
        nodesByDepth.set(depth, [])
      }
      nodesByDepth.get(depth)!.push(node)
    }

    // Position nodes
    const flowNodes: Array<Node> = []
    const xSpacing = 280
    const ySpacing = 100

    const sortedDepths = Array.from(nodesByDepth.keys()).sort((a, b) => a - b)
    const minDepth = sortedDepths[0] ?? 0

    for (const [depth, nodesAtDepth] of nodesByDepth) {
      const xPos = (depth - minDepth) * xSpacing
      const startY = -((nodesAtDepth.length - 1) * ySpacing) / 2

      nodesAtDepth.forEach((node, idx) => {
        const isSelected = node.keyPath === selectedNode
        flowNodes.push({
          id: node.keyPath,
          type: 'journey',
          position: { x: xPos, y: startY + idx * ySpacing },
          selected: isSelected,
          data: {
            label: node.keyPath.split('/').pop() || node.keyPath,
            isCurrent: node.keyPath === assetKey,
            computeKind: node.computeKind,
            assetKey: node.keyPath,
            onSelect: handleNodeSelect,
          } as JourneyNodeData,
        })
      })
    }

    // Create edges
    const flowEdges: Array<Edge> = graphData.edges.map((edge) => ({
      id: `${edge.source}-${edge.target}`,
      source: edge.source,
      target: edge.target,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: { stroke: 'var(--border)', strokeWidth: 2 },
      animated: edge.source === assetKey || edge.target === assetKey,
    }))

    return { nodes: flowNodes, edges: flowEdges }
  }, [graphData, assetKey, selectedNode, handleNodeSelect])

  const onInit = useCallback(() => {
    // Fit to view on init
  }, [])

  if (loading) {
    return (
      <div className={`flex items-center justify-center h-64 ${className}`}>
        <Loader2 className="size-8 text-primary animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div
        className={`flex items-center justify-center h-64 text-red-400 ${className}`}
      >
        <p>{error}</p>
      </div>
    )
  }

  if (nodes.length === 0) {
    return (
      <div
        className={`flex flex-col items-center justify-center h-64 text-muted-foreground ${className}`}
      >
        <Database className="size-8 mb-2 opacity-50" />
        <p>No lineage data available</p>
      </div>
    )
  }

  return (
    <div className={`space-y-4 ${className}`}>
      {/* Flow visualization */}
      <div className="h-72 bg-background border border-border">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={journeyNodeTypes}
          onInit={onInit}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          panOnDrag
          zoomOnScroll
        >
          <Background color="var(--border)" gap={16} />
          <Controls
            showInteractive={false}
            className="!bg-card !border-border !rounded-none [&>button]:!bg-card [&>button]:!border-border [&>button]:!fill-muted-foreground [&>button:hover]:!bg-muted"
          />
        </ReactFlow>
      </div>

      {/* Detail panel below flow */}
      <NodeDetailPanel
        assetKey={selectedNode || assetKey}
        isLoading={detailsLoading}
        details={nodeDetails}
        rowData={rowData}
        onQuerySource={onQuerySource}
      />
    </div>
  )
}
