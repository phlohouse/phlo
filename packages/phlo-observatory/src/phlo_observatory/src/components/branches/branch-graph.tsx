/** Lays out and renders branch commits and their relationship to main. */
import '@xyflow/react/dist/base.css'
import * as React from 'react'
import { ClientOnly } from '@tanstack/react-router'
import { Handle, Position, ReactFlow } from '@xyflow/react'
import type { BuiltInEdge, Edge, Node, NodeProps } from '@xyflow/react'
import type { Commit, BranchGraph as Graph } from '@/lib/data/types'
import { Tooltip } from '@/components/ui/menu'
import { Skeleton } from '@/components/ui/separator'
import { useTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'

type Props = {
  name: string
  graph: Graph
  /** Optional commit list; used for hover tooltips (hash + message). */
  commits?: Array<Commit>
  /** Colour the branch as conflicting with main. */
  conflicting?: boolean
}

const DX = 76
const MAIN_Y = 64
const BRANCH_Y = 22

type Kind = 'main' | 'main-head' | 'branch' | 'branch-head' | 'ghost'
type CommitData = {
  kind: Kind
  lane: 'main' | 'branch'
  hash?: string
  caption?: string
  tip?: string
  conflict?: boolean
}
type LabelData = { text: string; lane: 'main' | 'branch'; conflict?: boolean }
type CommitNode = Node<CommitData, 'commit'>
type LabelNode = Node<LabelData, 'label'>

const R = 6
const hidden = '!min-h-0 !min-w-0 !size-px !border-0 !bg-transparent !opacity-0'

function CommitView({ data }: NodeProps<CommitNode>) {
  const branchTone = data.conflict ? 'border-bad' : 'border-branch'
  const dot = cn(
    'block size-3 rounded-full border-2',
    data.kind === 'main' && 'border-text-3 bg-card',
    data.kind === 'main-head' && 'border-foreground bg-foreground',
    data.kind === 'branch' && cn(branchTone, 'bg-card'),
    data.kind === 'branch-head' &&
      cn(branchTone, data.conflict ? 'bg-bad' : 'bg-branch'),
    data.kind === 'ghost' && 'border-dashed border-branch bg-card',
  )
  const caption = data.caption ?? data.hash
  const circle = <span className={dot} />
  return (
    <div className="relative size-3">
      <Handle
        type="target"
        position={Position.Left}
        id="l"
        isConnectable={false}
        className={hidden}
      />
      <Handle
        type="target"
        position={Position.Top}
        id="t"
        isConnectable={false}
        className={hidden}
      />
      {data.tip ? <Tooltip content={data.tip}>{circle}</Tooltip> : circle}
      {caption ? (
        <span
          className={cn(
            'pointer-events-none absolute left-1/2 -translate-x-1/2 font-mono text-[11px] whitespace-nowrap',
            data.lane === 'main' ? 'top-[18px]' : 'bottom-[18px]',
            data.kind === 'ghost' ? 'text-branch' : 'text-muted-foreground',
          )}
        >
          {caption}
        </span>
      ) : null}
      <Handle
        type="source"
        position={Position.Right}
        id="r"
        isConnectable={false}
        className={hidden}
      />
      <Handle
        type="source"
        position={Position.Top}
        id="st"
        isConnectable={false}
        className={hidden}
      />
    </div>
  )
}

function LabelView({ data }: NodeProps<LabelNode>) {
  return (
    <span className="relative block">
      <Handle
        type="source"
        position={Position.Right}
        id="r"
        isConnectable={false}
        className={hidden}
      />
      <span
        className={cn(
          'inline-flex h-5 items-center rounded-[5px] px-1.5 font-mono text-[11px] whitespace-nowrap',
          data.lane === 'main'
            ? 'bg-soft text-text-2'
            : data.conflict
              ? 'bg-bad-soft text-bad-ink'
              : 'bg-branch-soft text-branch',
        )}
      >
        {data.text}
      </span>
    </span>
  )
}

const nodeTypes = { commit: CommitView, label: LabelView }

function build({ name, graph, commits = [], conflicting }: Props) {
  const msg = new Map(commits.map((c) => [c.id, c.message]))
  const tip = (h: string) => (msg.has(h) ? `${h} · ${msg.get(h)}` : h)
  const isMain = name === 'main'
  const hasBranch = graph.commits.length > 0 || !isMain
  const n = graph.commits.length
  const behindShown = Math.min(graph.behind, 3)
  const nodes: Array<CommitNode | LabelNode> = []
  const edges: Array<Edge | BuiltInEdge> = []
  const at = (col: number, lane: 'main' | 'branch') => ({
    x: col * DX - R,
    y: (lane === 'main' ? MAIN_Y : BRANCH_Y) - R,
  })
  const commit = (id: string, col: number, data: CommitData) =>
    nodes.push({
      id,
      type: 'commit',
      position: at(col, data.lane),
      width: 12,
      height: 12,
      data,
      selectable: false,
    })
  const line = (
    source: string,
    target: string,
    stroke: string,
    extra: Partial<Edge> = {},
  ) =>
    edges.push({
      id: `${source}-${target}`,
      source,
      target,
      sourceHandle: 'r',
      targetHandle: 'l',
      type: 'straight',
      style: { stroke, strokeWidth: 2 },
      focusable: false,
      ...extra,
    })

  const mainStroke = 'var(--border-strong)'
  const branchStroke = conflicting ? 'var(--bad)' : 'var(--branch)'

  // main: two earlier commits, the fork point, then whatever landed since.
  commit('m0', 0, { kind: 'main', lane: 'main' })
  commit('m1', 1, { kind: 'main', lane: 'main' })
  commit('base', 2, {
    kind: 'main-head',
    lane: 'main',
    hash: graph.base,
    tip: tip(graph.base),
  })
  line('m0', 'm1', mainStroke)
  line('m1', 'base', mainStroke)
  let mainTip = 'base'
  let mainCol = 2
  for (let i = 0; i < behindShown; i++) {
    const id = `ahead${i}`
    const last = i === behindShown - 1
    commit(id, 3 + i, {
      kind: 'main',
      lane: 'main',
      caption: last ? `+${graph.behind} on main` : undefined,
    })
    line(mainTip, id, mainStroke)
    mainTip = id
    mainCol = 3 + i
  }
  nodes.push({
    id: 'label:main',
    type: 'label',
    position: { x: -DX + 12, y: MAIN_Y - 10 },
    data: { text: 'main', lane: 'main' },
    selectable: false,
  })

  if (hasBranch) {
    const ids = n ? graph.commits : ['__empty']
    ids.forEach((h, i) => {
      const head = n > 0 && i === n - 1
      commit(`b:${h}`, 3 + (n > 1 ? (i * 2) / (n - 1) : 0.5), {
        kind: head ? 'branch-head' : 'branch',
        lane: 'branch',
        hash: n ? h : undefined,
        tip: n ? tip(h) : 'No commits yet',
        conflict: conflicting,
      })
      if (i === 0)
        edges.push({
          id: `fork-${h}`,
          source: 'base',
          target: `b:${h}`,
          sourceHandle: 'st',
          targetHandle: 'l',
          type: 'smoothstep',
          pathOptions: { borderRadius: 14 },
          style: { stroke: branchStroke, strokeWidth: 2 },
          focusable: false,
        })
      else line(`b:${ids[i - 1]}`, `b:${h}`, branchStroke)
    })
    const headCol = 3 + (n > 1 ? 2 : 0.5)
    nodes.push({
      id: 'label:branch',
      type: 'label',
      position: { x: headCol * DX + 16, y: BRANCH_Y - 10 },
      data: { text: name, lane: 'branch', conflict: conflicting },
      selectable: false,
    })
    if (graph.mergeable) {
      // land the merge past the branch label so the dashed drop doesn't cross it
      const col = Math.max(
        mainCol + 1,
        (headCol * DX + 16 + name.length * 6.7 + 14 + 28) / DX,
      )
      commit('merge', col, {
        kind: 'ghost',
        lane: 'main',
        caption: 'merge',
        tip: `Merging ${name} into main`,
      })
      edges.push({
        id: 'merge',
        source: 'label:branch',
        target: 'merge',
        sourceHandle: 'r',
        targetHandle: 't',
        type: 'smoothstep',
        pathOptions: { borderRadius: 14 },
        style: { stroke: branchStroke, strokeWidth: 2, strokeDasharray: '4 4' },
        focusable: false,
      })
      // main carries on to where the merge would land
      line(mainTip, 'merge', mainStroke, {
        style: { stroke: mainStroke, strokeWidth: 2, strokeDasharray: '4 4' },
      })
    }
  }
  // Rough extent for the phone scroller: labels are ~6.7px per monospace character at 11px.
  const xs = nodes.map((node) => [
    node.position.x,
    node.position.x +
      (node.type === 'label' ? node.data.text.length * 6.7 + 14 : 12),
  ])
  const minX = Math.min(...xs.map((v) => v[0]))
  const width = Math.max(...xs.map((v) => v[1])) - minX
  return { nodes, edges, width, baseLeft: 2 * DX - minX }
}

function Flow(props: Props & { label: string }) {
  const { theme } = useTheme()
  const { nodes, edges, width, baseLeft } = React.useMemo(
    () => build(props),
    [props.name, props.graph, props.commits, props.conflicting],
  )
  // Phones: draw at full size in a native scroller so touches scroll the page rather than the canvas.
  const [narrow] = React.useState(
    () => window.matchMedia('(max-width: 767px)').matches,
  )
  const scroller = React.useRef<HTMLDivElement>(null)
  React.useLayoutEffect(() => {
    const el = scroller.current
    if (el) el.scrollLeft = baseLeft - 80 // open on the fork point rather than old main history
  }, [narrow, baseLeft])
  const flow = (
    <ReactFlow
      className={cn('phlo-flow', narrow && 'phlo-flow-native')}
      aria-label={props.label}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      colorMode={theme}
      fitView
      fitViewOptions={{
        padding: { top: '26px', bottom: '28px', left: '12px', right: '56px' },
        maxZoom: narrow ? 1 : 1.15,
        minZoom: narrow ? 1 : 0.6,
      }}
      minZoom={0.5}
      maxZoom={2}
      nodesDraggable={false}
      nodesConnectable={false}
      nodesFocusable={false}
      edgesFocusable={false}
      elementsSelectable
      panOnDrag={!narrow}
      zoomOnPinch={!narrow}
      panOnScroll={false}
      zoomOnScroll={false}
      preventScrolling={false}
      zoomOnDoubleClick={false}
    />
  )
  if (!narrow) return flow
  return (
    <div
      ref={scroller}
      className="h-full overflow-x-auto overflow-y-hidden overscroll-x-contain"
    >
      <div className="h-full" style={{ width: width + 24, minWidth: '100%' }}>
        {flow}
      </div>
    </div>
  )
}

/** Fork-and-merge sketch: main along the bottom lane, the branch above it. */
export function BranchGraph({ name, graph, commits, conflicting }: Props) {
  const n = graph.commits.length
  const label =
    name === 'main'
      ? `main at commit ${graph.base}.`
      : `Branch ${name} forks from main at commit ${graph.base} and adds ${n} commit${n === 1 ? '' : 's'}; ${
          graph.behind
            ? `main has ${graph.behind} new commits since.`
            : 'main has no new commits since.'
        }${graph.mergeable ? ' It can merge into main.' : ''}${conflicting ? ' It conflicts with main.' : ''}`
  const msg = new Map((commits ?? []).map((c) => [c.id, c.message]))
  return (
    <div>
      <div
        role="img"
        aria-label={label}
        className={cn(
          'max-w-[820px]',
          n > 0 || name !== 'main' ? 'h-[132px]' : 'h-[88px]',
        )}
      >
        <ClientOnly fallback={<Skeleton className="h-full w-full" />}>
          <Flow
            key={name}
            name={name}
            graph={graph}
            commits={commits}
            conflicting={conflicting}
            label={label}
          />
        </ClientOnly>
      </div>
      <div className="sr-only">
        <p>{label}</p>
        <ul>
          <li>
            main: fork point {graph.base}
            {msg.get(graph.base) ? ` (${msg.get(graph.base)})` : ''}
            {graph.behind ? `, then ${graph.behind} newer commits` : ''}
          </li>
          {graph.commits.map((c) => (
            <li key={c}>
              {name}: {c}
              {msg.get(c) ? ` (${msg.get(c)})` : ''}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
