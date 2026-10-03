/** Lays out and renders asset lineage as an interactive flow graph. */
import '@xyflow/react/dist/base.css'
import * as React from 'react'
import { ClientOnly, Link } from '@tanstack/react-router'
import { Controls, Handle, Position, ReactFlow } from '@xyflow/react'
import type { Edge, Node, NodeProps } from '@xyflow/react'
import type {
  Env,
  Layer,
  LineageColumn,
  LineageNode,
  LineageTone,
} from '@/lib/data/types'
import { LayerSwatch } from '@/components/phlo/status'
import { Skeleton } from '@/components/ui/separator'
import { useTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'

type Props = {
  columns: Array<LineageColumn>
  edges: Array<[string, string]>
  label: string
  env: Env
}

const NODE_H = 56
const ROW = 76
const GAP = 64
const TOP = 34 // room for the column headings

type TableData = { node: LineageNode; stale: boolean; env: Env }
type HeadingData = { text: string }
type TableNode = Node<TableData, 'table'>
type HeadingNode = Node<HeadingData, 'heading'>

const layers: Array<Layer> = ['bronze', 'silver', 'gold']
const layerOf = (id: string) => layers.find((l) => id.startsWith(`${l}.`))

const box: Record<LineageTone, string> = {
  source: 'bg-sunken border-border-card',
  job: 'bg-card border-border-card',
  self: 'bg-card border-border-card',
  bad: 'bg-bad-wash border-bad-line',
  warn: 'bg-warn-wash border-border-card',
  ok: 'bg-card border-border-card',
}

function TableNodeView({ data, width }: NodeProps<TableNode>) {
  const n = data.node
  const self = n.tone === 'self'
  const tone: LineageTone = self && data.stale ? 'bad' : n.tone
  const layer = self ? undefined : layerOf(n.id)
  const dot = n.subBad ? 'bg-bad' : n.tone === 'warn' ? 'bg-warn' : null
  const body = (
    <>
      <span
        className={cn(
          'flex min-w-0 items-center gap-1.5 text-[11px] leading-4',
          n.subBad ? 'text-bad-text' : 'text-muted-foreground',
        )}
      >
        {dot ? (
          <span
            aria-hidden
            className={cn('size-[7px] shrink-0 rounded-full', dot)}
          />
        ) : null}
        <span className="truncate">{n.sub}</span>
      </span>
      <span className="flex min-w-0 items-center gap-1.5">
        {layer ? <LayerSwatch layer={layer} /> : null}
        <span
          className={cn(
            'truncate leading-5 text-foreground',
            n.tone === 'source' ? 'text-[13px]' : 'font-mono text-[12px]',
            self && 'font-semibold',
          )}
        >
          {n.name}
        </span>
      </span>
    </>
  )
  const cls = cn(
    'flex h-full w-full flex-col justify-center gap-0.5 rounded-lg border px-3.5 text-left no-underline',
    box[tone],
    self &&
      (data.stale
        ? 'border-bad ring-2 ring-bad/25'
        : 'border-primary ring-2 ring-primary/25'),
  )
  return (
    <div style={{ width, height: NODE_H }}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      {n.href ? (
        <Link
          to={n.href}
          search={{ env: data.env }}
          draggable={false}
          tabIndex={-1}
          aria-label={`${n.name}, ${n.sub}`}
          className={cn(
            cls,
            'transition-colors hover:border-border-strong hover:text-foreground focus-visible:outline-offset-2',
          )}
        >
          {body}
        </Link>
      ) : (
        <div className={cls}>{body}</div>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  )
}

function HeadingNodeView({ data }: NodeProps<HeadingNode>) {
  return (
    <div className="text-[11px] tracking-[0.6px] whitespace-nowrap text-muted-foreground uppercase">
      {data.text}
    </div>
  )
}

const nodeTypes = { table: TableNodeView, heading: HeadingNodeView }

function build(
  columns: Array<LineageColumn>,
  pairs: Array<[string, string]>,
  env: Env,
) {
  const rows = Math.max(1, ...columns.map((c) => c.nodes.length))
  const mid = TOP + ((rows - 1) * ROW) / 2
  const all = columns.flatMap((c) => c.nodes)
  const byId = new Map(all.map((n) => [n.id, n]))
  const stale = all.some((n) => n.tone === 'self' && n.subBad)
  const nodes: Array<TableNode | HeadingNode> = []
  let x = 0
  for (const c of columns) {
    const w = c.wide ? 210 : 190
    nodes.push({
      id: `h:${c.heading}`,
      type: 'heading',
      position: { x, y: 0 },
      data: { text: c.heading },
      selectable: false,
      focusable: false,
    })
    c.nodes.forEach((n, k) =>
      nodes.push({
        id: n.id,
        type: 'table',
        position: { x, y: mid + (k - (c.nodes.length - 1) / 2) * ROW },
        width: w,
        height: NODE_H,
        data: { node: n, stale, env },
      }),
    )
    x += w + GAP
  }
  const width = x - GAP
  const edges: Array<Edge> = pairs
    .filter(([a, b]) => byId.has(a) && byId.has(b))
    .map(([a, b]) => {
      const t = byId.get(b)!
      const hot =
        t.tone === 'bad' ||
        (t.tone === 'self' && t.subBad) ||
        (t.tone === 'job' && t.subBad)
      const warm = t.tone === 'warn'
      return {
        id: `${a}->${b}`,
        source: a,
        target: b,
        type: 'smoothstep',
        focusable: false,
        style: {
          stroke: hot
            ? 'var(--bad)'
            : warm
              ? 'var(--warn)'
              : 'var(--border-strong)',
          strokeWidth: 1.5,
        },
      }
    })
  return { nodes, edges, width }
}

function Flow({ columns, edges: pairs, label, env }: Props) {
  const { theme } = useTheme()
  const { nodes, edges, width } = React.useMemo(
    () => build(columns, pairs, env),
    [columns, pairs, env],
  )
  // Phones: draw at full size inside a native horizontal scroller, so one finger scrolls the page
  // (or slides the graph sideways) instead of being captured by the canvas.
  const [narrow] = React.useState(
    () => window.matchMedia('(max-width: 767px)').matches,
  )
  const scroller = React.useRef<HTMLDivElement>(null)
  const selfX =
    nodes.find((n) => n.type === 'table' && n.data.node.tone === 'self')
      ?.position.x ?? 0
  React.useLayoutEffect(() => {
    if (narrow && scroller.current)
      scroller.current.scrollLeft = Math.max(0, selfX + 16 - 24)
  }, [narrow, selfX])
  const flow = (
    <ReactFlow
      className={cn('phlo-flow', narrow && 'phlo-flow-native')}
      aria-label={label}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      colorMode={theme}
      fitView
      fitViewOptions={
        narrow
          ? { padding: '16px', minZoom: 1, maxZoom: 1 }
          : { padding: 0.06, maxZoom: 1, minZoom: 0.4 }
      }
      minZoom={0.3}
      maxZoom={1.5}
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
    >
      {narrow ? null : (
        <Controls
          showInteractive={false}
          orientation="horizontal"
          position="bottom-left"
        />
      )}
    </ReactFlow>
  )
  if (!narrow) return flow
  return (
    <div
      ref={scroller}
      className="h-full overflow-x-auto overflow-y-hidden overscroll-x-contain"
    >
      <div className="h-full" style={{ width: width + 32 }}>
        {flow}
      </div>
    </div>
  )
}

/** Upstream → this table → downstream, laid out left to right by depth. */
export function LineageGraph(props: Props) {
  const all = props.columns.flatMap((c) => c.nodes)
  const nameOf = (id: string) => all.find((m) => m.id === id)?.name ?? id
  // Phones draw at 100 %, so size the box to the graph (up to 420px) rather than leave it half empty.
  const rows = Math.max(1, ...props.columns.map((c) => c.nodes.length))
  const phoneH = Math.min(
    420,
    Math.max(260, TOP + (rows - 1) * ROW + NODE_H + 48),
  )
  return (
    <div className="relative">
      <div
        role="img"
        aria-label={props.label}
        style={{ '--flow-h': `${phoneH}px` } as React.CSSProperties}
        className="h-(--flow-h) overflow-hidden rounded-lg border border-border-card bg-sunken md:h-[360px]"
      >
        <ClientOnly
          fallback={<Skeleton className="h-full w-full rounded-none" />}
        >
          <Flow key={all.find((n) => n.tone === 'self')?.id} {...props} />
        </ClientOnly>
      </div>
      {/* The graph is a picture; this list carries the same facts and links for keyboards and screen readers. */}
      <nav
        aria-label="Lineage"
        className="sr-only focus-within:not-sr-only focus-within:absolute focus-within:top-2 focus-within:right-2 focus-within:z-10 focus-within:rounded-lg focus-within:border focus-within:border-border focus-within:bg-popover focus-within:p-3 focus-within:text-[13px] focus-within:shadow-dialog"
      >
        <ul className="flex flex-col gap-1">
          {all.map((n) => {
            const to = props.edges
              .filter(([a]) => a === n.id)
              .map(([, b]) => nameOf(b))
            return (
              <li key={n.id}>
                {n.href ? (
                  <Link to={n.href} search={{ env: props.env }}>
                    {n.name}
                  </Link>
                ) : (
                  n.name
                )}
                <span className="text-muted-foreground">
                  {' '}
                  · {n.sub}
                  {to.length ? ` · feeds ${to.join(', ')}` : ''}
                </span>
              </li>
            )
          })}
        </ul>
      </nav>
    </div>
  )
}
