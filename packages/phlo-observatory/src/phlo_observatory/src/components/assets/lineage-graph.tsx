/** Lays out and renders asset lineage as an interactive flow graph. */
import '@xyflow/react/dist/base.css'
import * as React from 'react'
import { ClientOnly, Link } from '@tanstack/react-router'
import { Controls, Handle, Position, ReactFlow } from '@xyflow/react'
import { ChevronRightIcon } from 'lucide-react'
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
  // Draw exactly the edges the lineage table lists.
  const edges: Array<Edge> = lineageRows(columns, pairs).edges.map(([a, b]) => {
    const t = byId.get(b)!
    const hot =
      t.tone === 'bad' ||
      (t.tone === 'self' && t.subBad) ||
      (t.tone === 'job' && t.subBad)
    const warm = t.tone === 'warn'
    return {
      id: JSON.stringify([a, b]),
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

/** The graph's nodes and directed edges, in column order, with each node's neighbours. */
export function lineageRows(
  columns: Array<LineageColumn>,
  pairs: Array<[string, string]>,
) {
  const all = columns.flatMap((c) =>
    c.nodes.map((node) => ({ node, position: c.heading })),
  )
  const byId = new Map(all.map((row) => [row.node.id, row.node]))
  // Same edges the graph draws: both ends present, each drawn once.
  const edges = [
    ...new Map(
      pairs
        .filter(([a, b]) => byId.has(a) && byId.has(b))
        .map(([a, b]): [string, [string, string]] => [
          JSON.stringify([a, b]),
          [a, b],
        ]),
    ).values(),
  ]
  const rows = all.map(({ node, position }) => ({
    node,
    position,
    upstream: edges.filter(([, b]) => b === node.id).map(([a]) => byId.get(a)!),
    downstream: edges
      .filter(([a]) => a === node.id)
      .map(([, b]) => byId.get(b)!),
  }))
  return { rows, edges }
}

function NodeLinks({ nodes, env }: { nodes: Array<LineageNode>; env: Env }) {
  if (!nodes.length) return <span className="text-muted-foreground">None</span>
  return (
    <ul className="flex flex-col gap-0.5">
      {nodes.map((n) => (
        <li key={n.id} className="break-all">
          <NodeName node={n} env={env} />
        </li>
      ))}
    </ul>
  )
}

function NodeName({ node, env }: { node: LineageNode; env: Env }) {
  if (node.tone === 'self')
    return (
      <span className="font-mono font-semibold">
        {node.name}
        <span className="sr-only"> (this asset)</span>
      </span>
    )
  return node.href ? (
    <Link
      to={node.href}
      search={{ env }}
      className="rounded-sm font-mono text-foreground underline decoration-border-strong underline-offset-2 hover:decoration-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      {node.name}
    </Link>
  ) : (
    <span className="font-mono">{node.name}</span>
  )
}

/** Upstream → this table → downstream, laid out left to right by depth. */
export function LineageGraph(props: Props) {
  const all = props.columns.flatMap((c) => c.nodes)
  const self = all.find((n) => n.tone === 'self')
  const { rows, edges } = lineageRows(props.columns, props.edges)
  const [open, setOpen] = React.useState(false)
  const panelId = React.useId()
  // Phones draw at 100 %, so size the box to the graph (up to 420px) rather than leave it half empty.
  const height = Math.max(1, ...props.columns.map((c) => c.nodes.length))
  const phoneH = Math.min(
    420,
    Math.max(260, TOP + (height - 1) * ROW + NODE_H + 48),
  )
  const summary = `${all.length} ${all.length === 1 ? 'asset' : 'assets'}, ${edges.length} ${edges.length === 1 ? 'relationship' : 'relationships'}`
  return (
    <div className="flex flex-col gap-2">
      {/* The canvas is pointer-only; the table below carries the same nodes, edges and links. */}
      <div
        role="group"
        aria-label={`${props.label} The lineage table after this graph lists the same assets and relationships.`}
        style={{ '--flow-h': `${phoneH}px` } as React.CSSProperties}
        className="h-(--flow-h) overflow-hidden rounded-lg border border-border-card bg-sunken md:h-[360px]"
      >
        <ClientOnly
          fallback={<Skeleton className="h-full w-full rounded-none" />}
        >
          <Flow key={self?.id} {...props} />
        </ClientOnly>
      </div>
      <div className="rounded-lg border border-line text-[13px]">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
          className="flex w-full items-center gap-1.5 rounded-lg px-3 py-2 text-left text-text-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <ChevronRightIcon
            aria-hidden
            className={cn('size-3.5 transition-transform', open && 'rotate-90')}
          />
          Lineage as a table · {summary}
        </button>
        <div
          id={panelId}
          hidden={!open}
          className="border-t border-line px-3 pt-2 pb-3"
        >
          {edges.length ? null : (
            <p className="pb-2 text-muted-foreground">
              No declared upstream or downstream assets
              {self ? ` for ${self.name}` : ''}.
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] border-collapse text-left">
              <caption className="sr-only">
                Lineage{self ? ` of ${self.name}` : ''}: each asset, where it
                sits, what feeds it and what it feeds.
              </caption>
              <thead>
                <tr className="text-xs text-muted-foreground">
                  <th scope="col" className="py-1.5 pr-3 font-normal">
                    Asset
                  </th>
                  <th scope="col" className="py-1.5 pr-3 font-normal">
                    Position
                  </th>
                  <th scope="col" className="py-1.5 pr-3 font-normal">
                    Fed by (upstream)
                  </th>
                  <th scope="col" className="py-1.5 font-normal">
                    Feeds (downstream)
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ node, position, upstream, downstream }) => (
                  <tr
                    key={node.id}
                    aria-current={node.tone === 'self' ? 'true' : undefined}
                    className={cn(
                      'border-t border-line-soft align-top',
                      node.tone === 'self' && 'bg-primary-soft',
                    )}
                  >
                    <th scope="row" className="py-1.5 pr-3 font-normal">
                      <span className="block break-all">
                        <NodeName node={node} env={props.env} />
                      </span>
                      <span
                        className={cn(
                          'block text-xs',
                          node.subBad
                            ? 'text-bad-text'
                            : 'text-muted-foreground',
                        )}
                      >
                        {node.sub}
                      </span>
                    </th>
                    <td className="py-1.5 pr-3 text-text-2">
                      {node.tone === 'self' ? (
                        <strong className="font-medium text-foreground">
                          This asset
                        </strong>
                      ) : (
                        position
                      )}
                    </td>
                    <td className="py-1.5 pr-3">
                      <NodeLinks nodes={upstream} env={props.env} />
                    </td>
                    <td className="py-1.5">
                      <NodeLinks nodes={downstream} env={props.env} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
