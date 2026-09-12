/**
 * Shared Journey Node component for React Flow visualizations.
 * Used by DataJourney and RowJourney components.
 */
import { Handle, Position } from '@xyflow/react'
import { Database } from 'lucide-react'

import type { NodeProps } from '@xyflow/react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export interface JourneyNodeData {
  label: string
  isCurrent: boolean
  computeKind?: string
  lastMaterialized?: string
  assetKey?: string
  onSelect?: (assetKey: string) => void
  [key: string]: unknown
}

export function JourneyNode({ data }: NodeProps) {
  const {
    label,
    isCurrent,
    computeKind,
    lastMaterialized,
    assetKey,
    onSelect,
  } = data as JourneyNodeData

  const isClickable = onSelect && assetKey

  const handleClick = () => {
    if (isClickable && assetKey != null) {
      onSelect(assetKey)
    }
  }

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (isClickable && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault()
      handleClick()
    }
  }

  const NodeElement = isClickable ? 'button' : 'div'

  return (
    <NodeElement
      onClick={isClickable ? handleClick : undefined}
      onKeyDown={isClickable ? handleKeyDown : undefined}
      aria-current={isCurrent ? 'true' : undefined}
      type={isClickable ? 'button' : undefined}
      className={cn(
        'border-rule bg-sheet border text-left',
        isClickable ? 'hover:bg-band cursor-pointer' : '',
        isCurrent && 'border-ink border-2',
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!bg-rule-soft"
      />

      <div className="px-4 py-3">
        <div className="mb-1 flex items-center gap-2">
          <Database
            className={cn('size-4', isCurrent ? 'text-ink' : 'text-ink-faint')}
          />
          <span className="text-ink font-mono text-xs font-bold">{label}</span>
        </div>

        <div className="flex items-center gap-2 text-xs">
          {computeKind && (
            <Badge variant={isClickable ? 'secondary' : 'outline'}>
              {computeKind}
            </Badge>
          )}
          {lastMaterialized && (
            <span className="text-muted-foreground">{lastMaterialized}</span>
          )}
        </div>
      </div>

      <Handle type="source" position={Position.Right} className="!bg-border" />
    </NodeElement>
  )
}
