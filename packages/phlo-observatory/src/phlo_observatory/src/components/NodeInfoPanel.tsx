/**
 * NodeInfoPanel Component
 *
 * Sidebar panel showing details of selected graph node.
 */

import { Link } from '@tanstack/react-router'
import {
  AlertTriangle,
  ArrowDownLeft,
  ArrowUpRight,
  ChevronDown,
  ChevronRight,
  Clock,
  Database,
  ExternalLink,
  GitBranch,
  X,
} from 'lucide-react'
import { useReducer, useState } from 'react'
import type { GraphNode, ImpactedAsset } from '@/observatory/api/graph'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { getAssetImpact } from '@/observatory/api/graph'
import { useObservatorySettings } from '@/hooks/useObservatorySettings'
import { formatDate } from '@/utils/dateFormat'

interface NodeInfoPanelProps {
  node: GraphNode | null
  onClose: () => void
  onFocusGraph: (keyPath: string) => void
}

export function NodeInfoPanel({
  node,
  onClose,
  onFocusGraph,
}: NodeInfoPanelProps) {
  const { settings } = useObservatorySettings()

  if (!node) return null

  const lastMaterialized = node.lastMaterialization
    ? formatTimeAgo(
        new Date(Number(node.lastMaterialization)),
        settings.ui.dateFormat,
      )
    : 'Never'

  return (
    <div className="w-80 bg-sheet border-l border-border flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-border">
        <div className="flex items-center gap-2">
          <Database className="size-5 text-primary" />
          <span className="font-medium">Asset Details</span>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose}>
          <X className="size-4 text-muted-foreground" />
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Name & Layer Badge */}
        <div>
          <h3 className="text-lg font-semibold break-all">{node.label}</h3>
          <div className="text-sm text-muted-foreground mt-1 break-all">
            {node.keyPath}
          </div>
          <div className="flex items-center gap-2 mt-2">
            <LayerBadge layer={node.layer} />
            {node.computeKind && (
              <Badge variant="outline">{node.computeKind}</Badge>
            )}
          </div>
        </div>

        {/* Description */}
        {node.description && (
          <div>
            <h4 className="text-sm font-medium text-muted-foreground mb-1">
              Description
            </h4>
            <p className="text-sm">{node.description}</p>
          </div>
        )}

        {/* Dependencies */}
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-muted/50 border border-border p-3">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <ArrowUpRight className="size-4" />
              <span className="text-xs font-medium">Upstream</span>
            </div>
            <div className="text-xl font-bold">{node.upstreamCount}</div>
          </div>
          <div className="bg-muted/50 border border-border p-3">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <ArrowDownLeft className="size-4" />
              <span className="text-xs font-medium">Downstream</span>
            </div>
            <div className="text-xl font-bold">{node.downstreamCount}</div>
          </div>
        </div>

        {/* Last Materialization */}
        <div className="bg-muted/50 border border-border p-3">
          <div className="flex items-center gap-2 text-muted-foreground mb-1">
            <Clock className="size-4" />
            <span className="text-xs font-medium">Last Materialized</span>
          </div>
          <div>{lastMaterialized}</div>
        </div>

        {/* Group */}
        {node.groupName && (
          <div>
            <h4 className="text-sm font-medium text-muted-foreground mb-1">
              Group
            </h4>
            <Badge variant="secondary" className="text-muted-foreground">
              {node.groupName}
            </Badge>
          </div>
        )}

        {/* Impact Analysis */}
        {node.downstreamCount > 0 && (
          <ImpactAnalysisSection
            key={node.keyPath}
            assetKey={node.keyPath}
            downstreamCount={node.downstreamCount}
            onFocusGraph={onFocusGraph}
          />
        )}
      </div>

      {/* Actions */}
      <div className="p-4 border-t border-border space-y-2">
        <Link
          to="/lineage"
          search={{ assetId: node.keyPath }}
          className={cn(
            buttonVariants({ size: 'sm' }),
            'w-full justify-center gap-2',
          )}
        >
          <ExternalLink className="size-4" />
          View Details
        </Link>
        <Button
          variant="outline"
          className="w-full justify-center gap-2"
          onClick={() => onFocusGraph(node.keyPath)}
        >
          <GitBranch className="size-4" />
          Focus on This Asset
        </Button>
      </div>
    </div>
  )
}

function LayerBadge({ layer }: { layer: string }) {
  const labelMap: Record<string, string> = {
    source: 'Source',
    bronze: 'Bronze',
    silver: 'Silver',
    gold: 'Gold',
    marts: 'Marts',
    publish: 'Published',
    unknown: 'Unknown',
  }

  return (
    <Badge variant="outline" className="text-muted-foreground">
      {labelMap[layer] || layer}
    </Badge>
  )
}

function formatTimeAgo(date: Date, mode: 'iso' | 'local'): string {
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return formatDate(date, mode)
}

interface ImpactAnalysisSectionProps {
  assetKey: string
  downstreamCount: number
  onFocusGraph: (keyPath: string) => void
}

type ImpactState = {
  impactedAssets: Array<ImpactedAsset>
  error: string | null
  loading: boolean
}

function ImpactAnalysisSection({
  assetKey,
  downstreamCount,
  onFocusGraph,
}: ImpactAnalysisSectionProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [{ impactedAssets, error, loading }, setImpactState] = useReducer(
    (current: ImpactState, next: Partial<ImpactState>) => ({
      ...current,
      ...next,
    }),
    { impactedAssets: [], error: null, loading: false },
  )
  const toggleExpanded = () => {
    const nextExpanded = !isExpanded
    setIsExpanded(nextExpanded)
    if (nextExpanded && impactedAssets.length === 0) {
      setImpactState({ error: null, loading: true })
      void getAssetImpact({
        data: { assetKey },
      })
        .then((result) =>
          setImpactState(
            'error' in result
              ? {
                  error: result.error ?? 'Failed to load impact analysis',
                  loading: false,
                }
              : { impactedAssets: result, error: null, loading: false },
          ),
        )
        .catch(() =>
          setImpactState({
            error: 'Failed to load impact analysis',
            loading: false,
          }),
        )
    }
  }

  const layerColors: Record<string, string> = {
    source: 'text-emerald-400',
    bronze: 'text-amber-400',
    silver: 'text-muted-foreground',
    gold: 'text-primary',
    marts: 'text-emerald-400',
    publish: 'text-lime-400',
    unknown: 'text-muted-foreground',
  }

  return (
    <div className="border border-primary/20 bg-primary/5 overflow-hidden">
      <button
        onClick={toggleExpanded}
        className="flex items-center justify-between w-full p-3 hover:bg-muted/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <AlertTriangle className="size-4 text-primary" />
          <span className="text-sm font-medium">Impact Analysis</span>
          <Badge variant="secondary" className="text-muted-foreground">
            {downstreamCount}
          </Badge>
        </div>
        {isExpanded ? (
          <ChevronDown className="size-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-4 text-muted-foreground" />
        )}
      </button>

      {isExpanded && (
        <div className="border-t border-border p-2 max-h-48 overflow-y-auto">
          {loading ? (
            <div className="text-sm text-muted-foreground text-center py-2">
              Loading…
            </div>
          ) : error ? (
            <div className="text-sm text-red-400 text-center py-2">{error}</div>
          ) : impactedAssets.length > 0 ? (
            <ul className="space-y-1">
              {impactedAssets.map((asset) => (
                <li key={asset.keyPath}>
                  <button
                    onClick={() => onFocusGraph(asset.keyPath)}
                    className="flex items-center gap-2 w-full px-2 py-1.5 text-left text-sm hover:bg-muted/50 transition-colors"
                  >
                    <span
                      className={`${layerColors[asset.layer]} font-medium truncate flex-1`}
                    >
                      {asset.label}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      +{asset.depth} hop{asset.depth > 1 ? 's' : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-sm text-muted-foreground text-center py-2">
              No downstream assets
            </div>
          )}
        </div>
      )}
    </div>
  )
}
