/**
 * The living lakehouse map: assets as live cards in stage lanes on a dotted
 * grid, dependency edges between them. Node state (health, checks,
 * publication, activity) renders on the card; selecting a node opens the
 * dock. Lanes are topological depth, labelled by dominant group.
 */
import { useMemo } from 'react'

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Database, FileInput, Layers, ShieldCheck, Table2 } from 'lucide-react'

import type { Edge, Node, NodeProps, NodeTypes } from '@xyflow/react'

import type { LakehouseMapModel, MapNodeModel } from './map-model'
import { HealthDot } from '@/components/observatory/status'
import { cn } from '@/lib/utils'

const COLUMN_W = 250
const ROW_H = 108
const LANE_HEADER_Y = -56

interface MapNodeData extends Record<string, unknown> {
  model: MapNodeModel
}

type MapFlowNode = Node<MapNodeData, 'lakehouse'>
type LaneFlowNode = Node<{ label: string; count: number }, 'lane'>

function kindIcon(kinds: Array<string>) {
  if (kinds.includes('dlt')) return FileInput
  if (kinds.includes('dbt')) return Layers
  if (kinds.includes('table') || kinds.includes('table_store')) return Table2
  if (kinds.includes('quality')) return ShieldCheck
  return Database
}

const stateGlow: Record<string, string> = {
  error: 'border-print-red/60 shadow-[0_0_16px_rgba(248,113,113,0.12)]',
  warning: 'border-amber-ink/50 shadow-[0_0_16px_rgba(245,184,61,0.1)]',
}

function LakehouseNode({ data, selected }: NodeProps<MapFlowNode>) {
  const { model } = data
  const Icon = kindIcon(model.kinds)
  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        style={{
          width: 6,
          height: 6,
          border: 0,
          background: 'var(--ink-faint)',
        }}
      />
      <div
        className={cn(
          'border-rule bg-panel w-[190px] cursor-pointer rounded-xl border px-3 py-2.5 transition-colors hover:border-ink-faint/50',
          stateGlow[model.state],
          model.activity === 'running' && 'border-blue/60',
          selected && 'border-blue ring-blue/40 ring-1',
        )}
      >
        <div className="flex items-center gap-2">
          <Icon className="text-ink-soft size-4 flex-none" />
          <span className="text-ink min-w-0 flex-1 truncate text-xs font-semibold">
            {model.label}
          </span>
          <span className="relative flex-none">
            <HealthDot state={model.state} />
            {model.activity === 'running' && (
              <span className="bg-blue absolute inset-0 animate-ping rounded-full opacity-60" />
            )}
          </span>
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <span className="bg-hover text-ink-soft rounded-full px-1.5 py-px text-[10px] font-medium">
            {model.group}
          </span>
          {model.publication && model.publication !== 'published' && (
            <span className="bg-status-band-warning text-amber-ink rounded-full px-1.5 py-px text-[10px] font-medium">
              {model.publication}
            </span>
          )}
          {model.checksTotal > 0 && (
            <span
              className={cn(
                'rounded-full px-1.5 py-px text-[10px] font-medium',
                model.checksFailing > 0
                  ? 'bg-status-band-error text-print-red'
                  : 'bg-status-band-ok text-ok-ink',
              )}
            >
              {model.checksTotal} checks
            </span>
          )}
          {model.activity === 'running' && (
            <span className="bg-blue-soft text-blue rounded-full px-1.5 py-px text-[10px] font-medium">
              running
            </span>
          )}
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        style={{
          width: 6,
          height: 6,
          border: 0,
          background: 'var(--ink-faint)',
        }}
      />
    </>
  )
}

function LaneHeader({ data }: NodeProps<LaneFlowNode>) {
  return (
    <div className="pointer-events-none w-[190px] select-none">
      <div className="text-ink-faint text-[10px] font-medium tracking-wider uppercase">
        {data.label}
      </div>
      <div className="text-ink-faint/60 font-mono text-[10px]">
        {data.count} {data.count === 1 ? 'node' : 'nodes'}
      </div>
    </div>
  )
}

const nodeTypes: NodeTypes = {
  lakehouse: LakehouseNode,
  lane: LaneHeader,
}

export function LakehouseMap({
  model,
  selectedId,
  onSelect,
}: {
  model: LakehouseMapModel
  selectedId?: string | null
  onSelect?: (id: string | null) => void
}) {
  const nodes = useMemo<Array<Node>>(() => {
    const flowNodes: Array<Node> = model.nodes.map(
      (node): MapFlowNode => ({
        id: node.id,
        type: 'lakehouse',
        position: { x: node.depth * COLUMN_W, y: node.row * ROW_H },
        data: { model: node },
        selected: node.id === selectedId,
      }),
    )
    const laneNodes: Array<Node> = model.lanes.map(
      (lane): LaneFlowNode => ({
        id: `lane:${lane.depth}`,
        type: 'lane',
        position: { x: lane.depth * COLUMN_W, y: LANE_HEADER_Y },
        data: { label: lane.label, count: lane.count },
        draggable: false,
        selectable: false,
        connectable: false,
        focusable: false,
      }),
    )
    return [...laneNodes, ...flowNodes]
  }, [model, selectedId])

  const edges = useMemo<Array<Edge>>(
    () =>
      model.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: 'smoothstep',
        animated: edge.active,
        style: {
          stroke: edge.active ? 'var(--blue)' : 'var(--rule)',
          strokeWidth: edge.active ? 1.75 : 1.25,
          opacity: edge.active ? 0.9 : 0.7,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: edge.active ? 'var(--blue)' : 'var(--rule)',
          width: 14,
          height: 14,
        },
      })),
    [model.edges],
  )

  if (model.nodes.length === 0) {
    return (
      <div className="text-ink-faint flex h-full min-h-96 flex-col items-center justify-center gap-2">
        <Database className="size-4" />
        <span className="text-xs">No assets mapped yet</span>
      </div>
    )
  }

  return (
    <div className="dotgrid bg-canvas h-full min-h-[32rem] w-full">
      <ReactFlow
        colorMode="dark"
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) =>
          node.type === 'lakehouse' && onSelect?.(node.id)
        }
        onPaneClick={() => onSelect?.(null)}
        fitView
        fitViewOptions={{ padding: 0.18, maxZoom: 1 }}
        minZoom={0.15}
        maxZoom={1.75}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          color="rgba(255,255,255,0.13)"
          gap={22}
          size={1}
        />
        <Controls showInteractive={false} position="bottom-left" />
        <MiniMap
          position="bottom-right"
          pannable
          zoomable
          nodeColor={(node) =>
            node.type === 'lane' ? 'transparent' : 'var(--ink-faint)'
          }
          maskColor="rgba(13,13,17,0.72)"
          style={{ background: 'var(--panel)' }}
        />
      </ReactFlow>
    </div>
  )
}
