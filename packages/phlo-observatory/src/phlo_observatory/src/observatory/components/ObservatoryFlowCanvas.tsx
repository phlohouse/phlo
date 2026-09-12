/**
 * React Flow canvas for observatory topology diagrams. Nodes are positioned
 * on fixed lanes (raw through marts, plus branch/quality/operation lanes) by
 * kind, with labeled edges between them.
 */
import { useCallback, useMemo } from 'react'

import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Database,
  GitBranch,
  RotateCcw,
  ShieldCheck,
  Table2,
} from 'lucide-react'

import type { Edge, Node, NodeProps, NodeTypes } from '@xyflow/react'
import type { MouseEvent } from 'react'

import { cn } from '@/lib/utils'

type ObservatoryFlowNodeKind =
  | 'asset'
  | 'table'
  | 'quality'
  | 'operation'
  | 'branch'
  | 'service'

export interface ObservatoryFlowNode {
  id: string
  label: string
  kind: ObservatoryFlowNodeKind
  selectId?: string | null
  lane?: string | null
  subtitle?: string | null
  metric?: string | null
}

export interface ObservatoryFlowEdge {
  id: string
  source: string
  target: string
  label?: string | null
}

interface FlowNodeData extends Record<string, unknown> {
  label: string
  kind: ObservatoryFlowNodeKind
  lane: string
  selectId?: string | null
  subtitle?: string | null
  metric?: string | null
}

const laneX: Record<string, number> = {
  raw: 0,
  bronze: 220,
  silver: 440,
  gold: 680,
  marts: 900,
  branch: 0,
  table: 260,
  publish: 540,
  quality: 540,
  operation: 260,
  service: 0,
  other: 820,
}

const kindIcon = {
  asset: Database,
  table: Table2,
  quality: ShieldCheck,
  operation: RotateCcw,
  branch: GitBranch,
  service: Database,
} satisfies Record<ObservatoryFlowNodeKind, typeof Database>

const kindTone: Record<ObservatoryFlowNodeKind, string> = {
  asset: 'text-primary',
  table: 'text-status-info',
  quality: 'text-status-ok',
  operation: 'text-status-warning',
  branch: 'text-status-info',
  service: 'text-muted-foreground',
}

const handleClass =
  '!size-1.5 !border-0 !bg-muted-foreground/60 !min-w-0 !min-h-0'

function FlowNode({ data, selected }: NodeProps<Node<FlowNodeData, 'phlo'>>) {
  const Icon = kindIcon[data.kind]

  return (
    <>
      <Handle type="target" position={Position.Left} className={handleClass} />
      <div
        className={cn(
          'bg-card ring-foreground/15 hover:ring-foreground/30 w-44 cursor-pointer px-2.5 py-2 ring-1 transition-shadow',
          selected && 'ring-primary ring-2',
        )}
      >
        <div className="flex items-center gap-1.5">
          <span className={cn('flex-none', kindTone[data.kind])}>
            <Icon className="size-3.5" />
          </span>
          <span className="text-foreground truncate text-[11px] font-medium">
            {data.label}
          </span>
        </div>
        <div className="text-muted-foreground mt-1 flex items-center justify-between gap-2 font-mono text-[9px] tracking-wide uppercase">
          <span className="truncate">{data.lane}</span>
          {data.metric && <span className="flex-none">{data.metric}</span>}
        </div>
      </div>
      <Handle type="source" position={Position.Right} className={handleClass} />
    </>
  )
}

const nodeTypes: NodeTypes = {
  phlo: FlowNode,
}

export function ObservatoryFlowCanvas({
  nodes: graphNodes,
  edges: graphEdges,
  selectedId,
  onSelect,
}: {
  nodes: Array<ObservatoryFlowNode>
  edges: Array<ObservatoryFlowEdge>
  selectedId?: string | null
  onSelect?: (id: string) => void
}) {
  const initialNodes = useMemo(() => {
    const laneCounts = new Map<string, number>()

    return graphNodes.map((graphNode): Node<FlowNodeData, 'phlo'> => {
      const lane = graphNode.lane || graphNode.kind || 'other'
      const index = laneCounts.get(lane) ?? 0
      laneCounts.set(lane, index + 1)

      return {
        id: graphNode.id,
        type: 'phlo',
        position: {
          x: laneX[lane] ?? laneX.other,
          y: index * 112,
        },
        data: {
          label: graphNode.label,
          kind: graphNode.kind,
          lane,
          selectId: graphNode.selectId ?? graphNode.id,
          subtitle: graphNode.subtitle,
          metric: graphNode.metric,
        },
        selected: graphNode.id === selectedId,
      }
    })
  }, [graphNodes, selectedId])

  const initialEdges = useMemo(
    () =>
      graphEdges.map(
        (edge): Edge => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: edge.label ?? undefined,
          type: 'smoothstep',
          style: { stroke: 'var(--border)', strokeWidth: 1.5 },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: 'var(--border)',
          },
        }),
      ),
    [graphEdges],
  )
  const canvasKey = `${selectedId ?? 'none'}:${graphNodes
    .map(
      (node) =>
        `${node.id}:${node.label}:${node.lane ?? ''}:${node.metric ?? ''}:${node.subtitle ?? ''}`,
    )
    .join('|')}:${graphEdges
    .map(
      (edge) => `${edge.id}:${edge.source}:${edge.target}:${edge.label ?? ''}`,
    )
    .join('|')}`

  return (
    <ObservatoryFlowCanvasInstance
      key={canvasKey}
      edges={initialEdges}
      nodes={initialNodes}
      onSelect={onSelect}
    />
  )
}

function ObservatoryFlowCanvasInstance({
  edges: initialEdges,
  nodes: initialNodes,
  onSelect,
}: {
  edges: Array<Edge>
  nodes: Array<Node<FlowNodeData, 'phlo'>>
  onSelect?: (id: string) => void
}) {
  const [nodes, , onNodesChange] = useNodesState(initialNodes)
  const [edges, , onEdgesChange] = useEdgesState(initialEdges)

  const handleNodeClick = useCallback(
    (_: MouseEvent, node: Node) => {
      onSelect?.(String(node.data.selectId ?? node.id))
    },
    [onSelect],
  )

  return (
    <div className="bg-surface-sunken h-full min-h-80 w-full">
      {nodes.length > 0 ? (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={handleNodeClick}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          fitView
          fitViewOptions={{ padding: 0.24, maxZoom: 0.9 }}
          minZoom={0.15}
          maxZoom={1.8}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="var(--border)" gap={20} />
          <Controls showInteractive={false} />
        </ReactFlow>
      ) : (
        <div className="text-muted-foreground flex h-full min-h-80 flex-col items-center justify-center gap-2">
          <Database className="size-4" />
          <span className="text-xs">No dependencies yet</span>
        </div>
      )}
    </div>
  )
}
