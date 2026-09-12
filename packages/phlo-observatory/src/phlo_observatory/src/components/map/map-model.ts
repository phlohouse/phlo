/**
 * Lakehouse map model: turns assets + datasets + checks + operations into a
 * lane-laid graph. Lanes are topological depth (longest path from a root),
 * labelled by the dominant asset group in the column. Ordering inside a
 * lane uses barycenter passes to keep edges uncrossed.
 */
import type {
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryOperation,
  ObservatoryQualityCheck,
} from '@/observatory/api/types'
import type { StatusState } from '@/components/observatory/status'
import { worstStatus } from '@/components/observatory/status'

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
}

export interface MapEdgeModel {
  id: string
  source: string
  target: string
  active: boolean
}

export interface MapLane {
  depth: number
  label: string
  count: number
}

export interface LakehouseMapModel {
  nodes: Array<MapNodeModel>
  edges: Array<MapEdgeModel>
  lanes: Array<MapLane>
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
}: {
  assets: Array<ObservatoryAsset>
  datasets: Array<ObservatoryDataset>
  quality: Array<ObservatoryQualityCheck>
  operations: Array<ObservatoryOperation>
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
  const activeIds = new Set(
    nodes
      .filter(
        (node) => node.activity === 'running' || node.activity === 'recent',
      )
      .map((node) => node.id),
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

  return { nodes, edges, lanes }
}
