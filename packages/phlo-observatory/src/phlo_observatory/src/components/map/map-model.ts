/**
 * Lakehouse map model: turns assets + datasets + checks + operations into a
 * lane-laid graph. Lanes are topological depth (longest path from a root),
 * labelled by the dominant asset group in the column. Ordering inside a
 * lane uses barycenter passes to keep edges uncrossed.
 *
 * Above FLAT_THRESHOLD assets the map clusters: groups collapse to tiles at
 * the overview, expanding one renders its members (or its connected
 * components when the group is still too large to read). focusId auto-
 * expands the containing unit and dims everything outside its 2-hop
 * neighborhood.
 */
import type {
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryOperation,
  ObservatoryQualityCheck,
} from '@/observatory/api/types'
import type { StatusState } from '@/components/observatory/status'
import { worstStatus } from '@/components/observatory/status'

/** Render individual cards below this many assets; cluster above it. */
export const FLAT_THRESHOLD = 60
/** A group larger than this expands to component tiles, not member cards. */
export const COMPONENT_THRESHOLD = 40
/** Max component tiles shown inside an expanded group; rest fold to "other". */
export const TILE_CAP = 40
/** Hard cap on simultaneously rendered member cards. */
export const CARD_CAP = 150

export interface MapNodeModel {
  id: string
  label: string
  group: string
  depth: number
  /** Row index inside its lane, after barycenter ordering. */
  row: number
  kinds: Array<string>
  state: StatusState
  publication?: string
  checksTotal: number
  checksFailing: number
  /** 'running' pulses, 'failed' glows, 'recent' marks a recently-touched node. */
  activity: 'running' | 'failed' | 'recent' | null
  table?: string
  description?: string
  /** Matched catalog dataset, when one exists. */
  datasetId?: string
  /** Faded when a focused asset exists and this node isn't within 2 hops. */
  dimmed?: boolean
}

export interface MapEdgeModel {
  id: string
  source: string
  target: string
  active: boolean
  /** Aggregated member-edge count — 1 for card-to-card edges. */
  weight: number
  /** Faded when focus exists and an endpoint is outside the neighborhood. */
  dimmed?: boolean
}

/** A collapsed unit: a whole group, or one connected component of a group. */
export interface MapClusterModel {
  /** Expansion key — 'grp:<group>' or 'cmp:<group>:<index>'. */
  id: string
  label: string
  group: string
  depth: number
  row: number
  count: number
  state: StatusState
  checksTotal: number
  checksFailing: number
  running: number
  failed: number
  /** True when the focused asset lives inside this cluster. */
  containsFocus: boolean
  /** Faded when a focused asset exists and no member is within 2 hops. */
  dimmed?: boolean
  memberIds: Array<string>
}

export interface MapLane {
  depth: number
  label: string
  count: number
}

export interface LakehouseMapModel {
  /** True when the map clustered — nodes then hold only expanded members. */
  clustered: boolean
  nodes: Array<MapNodeModel>
  clusters: Array<MapClusterModel>
  edges: Array<MapEdgeModel>
  lanes: Array<MapLane>
  /** Members hidden by CARD_CAP inside the expanded unit. */
  truncated: number
  /** The unit currently rendered as cards (explicit or via focus). */
  expandedUnit: { id: string; label: string } | null
}

const GROUP_LABELS: Record<string, string> = {
  ingest: 'Ingest',
  source: 'Source',
  raw: 'Source',
  registry: 'Registry',
  bronze: 'Bronze',
  staging: 'Staging',
  silver: 'Silver',
  transform: 'Transform',
  model: 'Transform',
  gold: 'Gold',
  publish: 'Publish',
  serving: 'Serving',
  mart: 'Serving',
  delivery: 'Serving',
}

export function laneLabel(group: string): string {
  return GROUP_LABELS[group.toLowerCase()] ?? group
}

/** Longest-path depth per node; cycles degrade to first-seen depth. */
export function computeDepths(
  assets: Array<ObservatoryAsset>,
): Map<string, number> {
  const ids = new Set(assets.map((asset) => asset.id))
  const memo = new Map<string, number>()
  const visiting = new Set<string>()
  const byId = new Map(assets.map((asset) => [asset.id, asset]))

  function depth(id: string): number {
    const seen = memo.get(id)
    if (seen !== undefined) return seen
    if (visiting.has(id)) return 0
    visiting.add(id)
    const asset = byId.get(id)
    const parents = (asset?.dependencies ?? []).filter((dep) => ids.has(dep))
    const value =
      parents.length === 0
        ? 0
        : 1 + Math.max(...parents.map((parent) => depth(parent)))
    visiting.delete(id)
    memo.set(id, value)
    return value
  }

  for (const asset of assets) depth(asset.id)
  return memo
}

/** Barycenter ordering inside each lane, parents then children sweeps. */
export function orderLane(
  lane: Array<string>,
  parentsOf: Map<string, Array<string>>,
  childrenOf: Map<string, Array<string>>,
  rowIndexOf: Map<string, number>,
): Array<string> {
  const ordered = [...lane].sort((a, b) => a.localeCompare(b))
  for (let sweep = 0; sweep < 3; sweep += 1) {
    const neighborLists = sweep % 2 === 0 ? parentsOf : childrenOf
    ordered.sort((a, b) => {
      const score = (id: string) => {
        const positions = (neighborLists.get(id) ?? [])
          .map((n) => rowIndexOf.get(n))
          .filter((p): p is number => p !== undefined)
        if (!positions.length) return Number.POSITIVE_INFINITY
        return positions.reduce((sum, p) => sum + p, 0) / positions.length
      }
      const sa = score(a)
      const sb = score(b)
      if (sa === sb) return a.localeCompare(b)
      return sa - sb
    })
    ordered.forEach((id, index) => rowIndexOf.set(id, index))
  }
  return ordered
}

function nodeActivity(
  assetId: string,
  operations: Array<ObservatoryOperation>,
): MapNodeModel['activity'] {
  const touching = operations.filter((operation) =>
    [operation.target?.id, operation.target?.label].includes(assetId),
  )
  if (touching.some((operation) => operation.status === 'running')) {
    return 'running'
  }
  if (touching.some((operation) => operation.status === 'failed')) {
    return 'failed'
  }
  return touching.length > 0 ? 'recent' : null
}

function datasetForAsset(
  asset: ObservatoryAsset,
  datasets: Array<ObservatoryDataset>,
): ObservatoryDataset | undefined {
  return (
    datasets.find((dataset) => dataset.id === asset.id) ??
    datasets.find((dataset) =>
      dataset.source_refs.some(
        (ref) => ref.id === asset.id || ref.id === asset.name,
      ),
    )
  )
}

export function buildLakehouseMap({
  assets,
  datasets,
  quality,
  operations,
  expanded = null,
  focusId = null,
}: {
  assets: Array<ObservatoryAsset>
  datasets: Array<ObservatoryDataset>
  quality: Array<ObservatoryQualityCheck>
  operations: Array<ObservatoryOperation>
  /** Cluster id to render as member cards — 'grp:x' or 'cmp:g:i'. */
  expanded?: string | null
  /** Focused asset — auto-expands its unit and dims its non-neighbors. */
  focusId?: string | null
}): LakehouseMapModel {
  const depths = computeDepths(assets)
  const checksByAsset = new Map<string, Array<ObservatoryQualityCheck>>()
  for (const check of quality) {
    const list = checksByAsset.get(check.asset_id) ?? []
    list.push(check)
    checksByAsset.set(check.asset_id, list)
  }

  const parentsOf = new Map<string, Array<string>>()
  const childrenOf = new Map<string, Array<string>>()
  const edges: Array<MapEdgeModel> = []
  const ids = new Set(assets.map((asset) => asset.id))
  for (const asset of assets) {
    for (const dep of asset.dependencies ?? []) {
      if (!ids.has(dep)) continue
      edges.push({
        id: `${dep}->${asset.id}`,
        source: dep,
        target: asset.id,
        active: false,
        weight: 1,
      })
      parentsOf.set(asset.id, [...(parentsOf.get(asset.id) ?? []), dep])
      childrenOf.set(dep, [...(childrenOf.get(dep) ?? []), asset.id])
    }
  }

  const nodes = assets.map((asset): MapNodeModel => {
    const dataset = datasetForAsset(asset, datasets)
    const checks = checksByAsset.get(asset.id) ?? []
    const activity = nodeActivity(asset.id, operations)
    const checkStates = checks.map((check) =>
      check.status === 'failing'
        ? ('error' as const)
        : check.status === 'warning'
          ? ('warning' as const)
          : check.status === 'passing'
            ? ('ok' as const)
            : ('unknown' as const),
    )
    const state = worstStatus([
      ...(dataset?.readiness_state
        ? [dataset.readiness_state as StatusState]
        : []),
      ...checkStates,
      activity === 'failed' ? 'error' : 'ok',
    ])
    return {
      id: asset.id,
      label: asset.name,
      group: asset.group ?? 'other',
      depth: depths.get(asset.id) ?? 0,
      row: 0,
      kinds: asset.kinds,
      state,
      publication: dataset?.publication_state,
      checksTotal: checks.length,
      checksFailing: checks.filter((check) => check.status === 'failing')
        .length,
      activity,
      table:
        typeof asset.metadata.table === 'string'
          ? asset.metadata.table
          : undefined,
      description: asset.description ?? undefined,
      datasetId: dataset?.id,
    }
  })

  // Order nodes inside each lane and record the row index.
  const byDepth = new Map<number, Array<string>>()
  for (const node of nodes) {
    byDepth.set(node.depth, [...(byDepth.get(node.depth) ?? []), node.id])
  }
  const rowIndexOf = new Map<string, number>()
  for (const [, lane] of [...byDepth.entries()].sort((a, b) => a[0] - b[0])) {
    for (const [index, id] of orderLane(
      lane,
      parentsOf,
      childrenOf,
      rowIndexOf,
    ).entries()) {
      rowIndexOf.set(id, index)
    }
  }
  for (const node of nodes) {
    node.row = rowIndexOf.get(node.id) ?? 0
  }
  // Only live work lights an edge — 'recent' is a node state, not flow.
  const activeIds = new Set(
    nodes.filter((node) => node.activity === 'running').map((node) => node.id),
  )
  for (const edge of edges) {
    edge.active = activeIds.has(edge.source) || activeIds.has(edge.target)
  }

  const lanes: Array<MapLane> = [...byDepth.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([depth, laneIds]) => {
      const counts = new Map<string, number>()
      for (const id of laneIds) {
        const group = nodes.find((node) => node.id === id)?.group ?? 'other'
        counts.set(group, (counts.get(group) ?? 0) + 1)
      }
      const dominant = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]
      return {
        depth,
        label: laneLabel(dominant?.[0] ?? `Stage ${depth + 1}`),
        count: laneIds.length,
      }
    })
  // Repeated lane labels read as bugs — disambiguate with a stage number.
  const labelCounts = new Map<string, number>()
  for (const lane of lanes) {
    labelCounts.set(lane.label, (labelCounts.get(lane.label) ?? 0) + 1)
  }
  const labelSeen = new Map<string, number>()
  for (const lane of lanes) {
    if ((labelCounts.get(lane.label) ?? 0) > 1) {
      const index = (labelSeen.get(lane.label) ?? 0) + 1
      labelSeen.set(lane.label, index)
      lane.label = `${lane.label} ${index}`
    }
  }

  // Focus neighborhood: 2 hops out over the undirected dependency graph.
  const neighborhood = focusId
    ? neighborhoodOf(focusId, parentsOf, childrenOf)
    : null

  if (nodes.length <= FLAT_THRESHOLD) {
    if (neighborhood) {
      for (const node of nodes) {
        node.dimmed = node.id !== focusId && !neighborhood.has(node.id)
      }
      for (const edge of edges) {
        edge.dimmed =
          (edge.source !== focusId && !neighborhood.has(edge.source)) ||
          (edge.target !== focusId && !neighborhood.has(edge.target))
      }
    }
    return {
      clustered: false,
      clusters: [],
      edges,
      expandedUnit: null,
      lanes,
      nodes,
      truncated: 0,
    }
  }

  return clusterModel({ edges, expanded, focusId, neighborhood, nodes })
}

/** Two-hop undirected neighborhood around a node. */
function neighborhoodOf(
  id: string,
  parentsOf: Map<string, Array<string>>,
  childrenOf: Map<string, Array<string>>,
): Set<string> {
  const seen = new Set<string>([id])
  let frontier = [id]
  for (let hop = 0; hop < 2; hop += 1) {
    const next: Array<string> = []
    for (const current of frontier) {
      for (const neighbor of [
        ...(parentsOf.get(current) ?? []),
        ...(childrenOf.get(current) ?? []),
      ]) {
        if (!seen.has(neighbor)) {
          seen.add(neighbor)
          next.push(neighbor)
        }
      }
    }
    frontier = next
  }
  return seen
}

/**
 * Clustered model: the zoom ladder is group tile → component tile → cards.
 *
 * - Collapsed groups render one tile each.
 * - An expanded small group renders member cards.
 * - An expanded big group renders component tiles — members partitioned by
 *   shared root ancestors (the pipeline that feeds them), labelled by the
 *   deepest member (the sub-pipeline's output).
 * - An expanded component renders its member cards, capped at CARD_CAP
 *   sorted by interestingness (failing, running, then name).
 * - focusId auto-expands the containing unit and dims non-neighbors.
 */
function clusterModel({
  edges,
  expanded,
  focusId,
  neighborhood,
  nodes,
}: {
  edges: Array<MapEdgeModel>
  expanded: string | null
  focusId: string | null
  neighborhood: Set<string> | null
  nodes: Array<MapNodeModel>
}): LakehouseMapModel {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const parentsOf = new Map<string, Array<string>>()
  for (const edge of edges) {
    parentsOf.set(edge.target, [
      ...(parentsOf.get(edge.target) ?? []),
      edge.source,
    ])
  }

  const groups = new Map<string, Array<MapNodeModel>>()
  for (const node of nodes) {
    groups.set(node.group, [...(groups.get(node.group) ?? []), node])
  }

  // Root-ancestor set per asset — the pipeline identity.
  const rootsetOf = new Map<string, string>()
  const rootMemo = new Map<string, Array<string>>()
  const rootsOf = (id: string, trail: Set<string>): Array<string> => {
    const memo = rootMemo.get(id)
    if (memo) return memo
    if (trail.has(id)) return []
    trail.add(id)
    const parents = parentsOf.get(id) ?? []
    const roots =
      parents.length === 0
        ? [id]
        : [...new Set(parents.flatMap((p) => rootsOf(p, trail)))]
    trail.delete(id)
    rootMemo.set(id, roots)
    return roots
  }
  for (const node of nodes) {
    rootsetOf.set(node.id, rootsOf(node.id, new Set()).sort().join('+'))
  }

  // Each group = one expandable unit; big groups split into components.
  interface Component {
    id: string
    label: string
    memberIds: Array<string>
  }
  interface Group {
    id: string
    label: string
    name: string
    components: Array<Component>
    memberIds: Array<string>
  }
  const groupList: Array<Group> = [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, members]) => {
      const group: Group = {
        id: `grp:${name}`,
        label: laneLabel(name),
        name,
        components: [],
        memberIds: members.map((node) => node.id),
      }
      if (members.length <= COMPONENT_THRESHOLD) {
        group.components = [
          { id: group.id, label: group.label, memberIds: group.memberIds },
        ]
        return group
      }
      const byRootset = new Map<string, Array<string>>()
      for (const member of members) {
        const key = rootsetOf.get(member.id) ?? member.id
        byRootset.set(key, [...(byRootset.get(key) ?? []), member.id])
      }
      let components = [...byRootset.values()].sort(
        (a, b) => b.length - a.length || a[0].localeCompare(b[0]),
      )
      // Cap visible tiles; the rest fold into an "other" component.
      if (components.length > TILE_CAP) {
        const rest = components.slice(TILE_CAP - 1).flat()
        components = [...components.slice(0, TILE_CAP - 1), rest]
      }
      group.components = components.map((memberIds, i) => {
        const sink = memberIds
          .map((id) => byId.get(id)!)
          .sort((a, b) => b.depth - a.depth || a.id.localeCompare(b.id))[0]
        const isOther = i === TILE_CAP - 1 && components.length === TILE_CAP
        return {
          id: `cmp:${name}:${i}`,
          label: isOther
            ? `${group.label} · other`
            : (sink?.label ?? `${group.label} ${i + 1}`),
          memberIds,
        }
      })
      return group
    })

  // Resolve expansion: 'grp:x' opens a group (component tiles, or member
  // cards when the group wasn't split); 'cmp:x:i' renders that component's
  // cards. focusId auto-expands its containing component.
  const componentOf = new Map<string, { component: Component; group: Group }>()
  for (const group of groupList) {
    for (const component of group.components) {
      for (const id of component.memberIds) {
        componentOf.set(id, { component, group })
      }
    }
  }
  const cardComponents = new Set<string>()
  let openGroup: Group | null = null
  let expandedUnit: { id: string; label: string } | null = null

  if (expanded?.startsWith('cmp:')) {
    for (const group of groupList) {
      const component = group.components.find((c) => c.id === expanded)
      if (component) {
        cardComponents.add(component.id)
        openGroup = group
        expandedUnit = { id: component.id, label: component.label }
        break
      }
    }
  } else if (expanded?.startsWith('grp:')) {
    const group = groupList.find((g) => g.id === expanded)
    if (group) {
      openGroup = group
      expandedUnit = { id: group.id, label: group.label }
      if (group.components.length === 1) {
        cardComponents.add(group.components[0].id)
      }
    }
  }
  const focusEntry = focusId ? componentOf.get(focusId) : undefined
  if (focusEntry) {
    openGroup = focusEntry.group
    cardComponents.add(focusEntry.component.id)
    expandedUnit = {
      id: focusEntry.component.id,
      label: focusEntry.component.label,
    }
  }

  // Render items: cards for expanded components; tiles for everything else —
  // a closed group renders its group tile, an open group its component tiles.
  const cards: Array<MapNodeModel> = []
  const clusters: Array<MapClusterModel> = []
  let truncated = 0

  const interestingness = (a: MapNodeModel, b: MapNodeModel) => {
    const score = (n: MapNodeModel) =>
      (n.state === 'error' ? 4 : 0) +
      (n.state === 'warning' ? 2 : 0) +
      (n.activity === 'running' ? 1 : 0)
    return score(b) - score(a) || a.depth - b.depth || a.id.localeCompare(b.id)
  }

  const tileFor = (
    unit: { id: string; label: string; memberIds: Array<string> },
    group: string,
  ): MapClusterModel => {
    const members = unit.memberIds.map((id) => byId.get(id)!)
    const depthCounts = new Map<number, number>()
    for (const member of members) {
      depthCounts.set(member.depth, (depthCounts.get(member.depth) ?? 0) + 1)
    }
    return {
      id: unit.id,
      label: unit.label,
      group,
      depth: [...depthCounts.entries()].sort((a, b) => b[1] - a[1])[0][0],
      row: members.reduce((sum, m) => sum + m.row, 0) / members.length,
      count: members.length,
      state: worstStatus(members.map((member) => member.state)),
      checksTotal: members.reduce((sum, m) => sum + m.checksTotal, 0),
      checksFailing: members.reduce((sum, m) => sum + m.checksFailing, 0),
      running: members.filter((m) => m.activity === 'running').length,
      failed: members.filter((m) => m.activity === 'failed').length,
      containsFocus: focusId !== null && unit.memberIds.includes(focusId),
      memberIds: unit.memberIds,
    }
  }

  for (const group of groupList) {
    if (group === openGroup) {
      for (const component of group.components) {
        if (cardComponents.has(component.id)) {
          const shown = component.memberIds
            .map((id) => byId.get(id)!)
            .sort(interestingness)
            .slice(0, CARD_CAP)
          truncated += component.memberIds.length - shown.length
          cards.push(...shown)
        } else {
          clusters.push(tileFor(component, group.name))
        }
      }
    } else {
      clusters.push(tileFor(group, group.name))
    }
  }

  // Layout: distinct depths become rendered columns in flow order. Items in
  // a column wrap into sub-columns of WRAP_ROWS — a 40-member expansion or
  // 39 component tiles read as a wall, not a tower. Cards take sub-column 0.
  const WRAP_ROWS = 14
  const renderedDepths = [
    ...new Set([...cards, ...clusters].map((i) => i.depth)),
  ].sort((a, b) => a - b)
  let nextCol = 0
  for (const depth of renderedDepths) {
    const base = nextCol
    const items = [
      ...cards
        .filter((card) => card.depth === depth)
        .sort((a, b) => a.row - b.row),
      ...clusters
        .filter((cluster) => cluster.depth === depth)
        .sort((a, b) => a.row - b.row),
    ]
    items.forEach((item, index) => {
      item.depth = base + Math.floor(index / WRAP_ROWS)
      item.row = index % WRAP_ROWS
    })
    nextCol += Math.max(1, Math.ceil(items.length / WRAP_ROWS))
  }

  // Aggregate edges onto whatever each endpoint renders as.
  const itemOf = new Map<string, string>()
  for (const card of cards) itemOf.set(card.id, card.id)
  for (const cluster of clusters) {
    for (const id of cluster.memberIds) itemOf.set(id, cluster.id)
  }
  const aggregated = new Map<string, MapEdgeModel>()
  for (const edge of edges) {
    const source = itemOf.get(edge.source)
    const target = itemOf.get(edge.target)
    if (!source || !target || source === target) continue
    const key = `${source}->${target}`
    const existing = aggregated.get(key)
    if (existing) {
      existing.weight += 1
      existing.active = existing.active || edge.active
    } else {
      aggregated.set(key, {
        id: key,
        source,
        target,
        active: edge.active,
        weight: 1,
      })
    }
  }

  if (neighborhood && focusId) {
    const dimItems = new Set(
      [...itemOf.entries()]
        .filter(
          ([memberId]) => memberId !== focusId && !neighborhood.has(memberId),
        )
        .map(([, itemId]) => itemId),
    )
    for (const card of cards) card.dimmed = dimItems.has(card.id)
    for (const cluster of clusters) {
      if (dimItems.has(cluster.id) && !cluster.containsFocus) {
        cluster.dimmed = true
      }
    }
    // An edge stays lit only when both endpoints are in the neighborhood.
    for (const edge of aggregated.values()) {
      edge.dimmed = dimItems.has(edge.source) || dimItems.has(edge.target)
    }
  }

  // Lanes describe rendered columns, not raw depths — clustered mode leaves
  // most depth columns empty, and per-depth headers just widen the canvas.
  const laneItems = new Map<
    number,
    { labels: Map<string, number>; count: number }
  >()
  for (const card of cards) {
    const entry = laneItems.get(card.depth) ?? { labels: new Map(), count: 0 }
    entry.labels.set(card.group, (entry.labels.get(card.group) ?? 0) + 1)
    entry.count += 1
    laneItems.set(card.depth, entry)
  }
  for (const cluster of clusters) {
    const entry = laneItems.get(cluster.depth) ?? {
      labels: new Map(),
      count: 0,
    }
    entry.labels.set(
      cluster.group,
      (entry.labels.get(cluster.group) ?? 0) + cluster.count,
    )
    entry.count += cluster.count
    laneItems.set(cluster.depth, entry)
  }
  const renderedLanes: Array<MapLane> = [...laneItems.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([depth, entry]) => {
      const dominant = [...entry.labels.entries()].sort(
        (a, b) => b[1] - a[1],
      )[0]
      return {
        depth,
        label: laneLabel(dominant?.[0] ?? `Stage ${depth + 1}`),
        count: entry.count,
      }
    })
  const labelCounts = new Map<string, number>()
  for (const lane of renderedLanes) {
    labelCounts.set(lane.label, (labelCounts.get(lane.label) ?? 0) + 1)
  }
  const labelSeen = new Map<string, number>()
  for (const lane of renderedLanes) {
    if ((labelCounts.get(lane.label) ?? 0) > 1) {
      const index = (labelSeen.get(lane.label) ?? 0) + 1
      labelSeen.set(lane.label, index)
      lane.label = `${lane.label} ${index}`
    }
  }

  return {
    clustered: true,
    clusters,
    edges: [...aggregated.values()],
    expandedUnit,
    lanes: renderedLanes,
    nodes: cards,
    truncated,
  }
}
