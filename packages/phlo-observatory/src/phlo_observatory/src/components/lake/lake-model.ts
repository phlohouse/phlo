/**
 * The Lake — the deck's center surface.
 *
 * The lakehouse's contents are datasets, not pipeline assets: the map of
 * *how* things are computed belongs to the orchestrator. What only this
 * console can show is what is actually in the lake — each dataset's
 * publication state, readiness, quality, and freshness — plus what is in
 * flight toward it: pipeline-run branches moving through write → audit →
 * publish.
 *
 * The surface is organized as columns (the layer of the lake a dataset
 * lives in, from its producing asset's group) of cells sorted worst-first,
 * with an inbound strip of in-flight runs across the top.
 */

import type {
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryOperation,
  ObservatoryQualityCheck,
} from '@/observatory/api/types'

import type { StatusState } from '@/components/observatory/status'

export interface LakeCell {
  id: string
  name: string
  /** Column key — the producing asset's group. */
  group: string
  state: StatusState
  readiness: StatusState
  publication: string
  owner?: string | null
  checksTotal: number
  checksFailing: number
  /** Pipeline freshness timestamp, when known. */
  freshnessAt?: string | null
  /** In-flight ops targeting this dataset's source assets. */
  inbound: number
  /** Focus ref for the inspector. */
  focusTarget: string
  /** Highlighted when this dataset is the focused object. */
  focused: boolean
}

export interface LakeInbound {
  id: string
  label: string
  total: number
  running: number
  failed: number
  /** Up to three target labels, then `+N`. */
  targets: Array<string>
  targetExtra: number
  startedAt?: string | null
  state: StatusState
  /** Focus ref for the most interesting op in the run. */
  focusTarget: string
}

export interface LakeColumn {
  id: string
  label: string
  cells: Array<LakeCell>
  count: number
  error: number
  warning: number
}

export interface LakeModel {
  inbound: Array<LakeInbound>
  columns: Array<LakeColumn>
  total: number
}

const STAGE_ORDER = [
  'source',
  'ingest',
  'raw',
  'staging',
  'intermediate',
  'transform',
  'marts',
  'publish',
  'serving',
  'export',
]

const CELL_CAP = 120

function stageRank(group: string): number {
  const index = STAGE_ORDER.indexOf(group)
  return index === -1 ? STAGE_ORDER.length : index
}

function columnLabel(group: string): string {
  return group.replace(/[-_]+/g, ' ')
}

function worst(...states: Array<StatusState>): StatusState {
  if (states.includes('error')) return 'error'
  if (states.includes('warning')) return 'warning'
  if (states.includes('ok')) return 'ok'
  return 'unknown'
}

function scopeLabel(scope: string): string {
  return scope.replace(/^pipeline-run-/, 'run ')
}

export function buildLakeModel({
  assets,
  datasets,
  focus,
  operations,
  pipelines,
  quality,
}: {
  assets: Array<ObservatoryAsset>
  datasets: Array<ObservatoryDataset>
  focus: { kind: string; id: string } | null
  operations: Array<ObservatoryOperation>
  pipelines: Array<ObservatoryDatasetPipeline>
  quality: Array<ObservatoryQualityCheck>
}): LakeModel {
  const assetById = new Map(assets.map((asset) => [asset.id, asset]))

  const checksByAsset = new Map<string, { failing: number; total: number }>()
  for (const check of quality) {
    if (!check.asset_id) continue
    const entry = checksByAsset.get(check.asset_id) ?? { failing: 0, total: 0 }
    entry.total += 1
    if (check.status === 'failing') entry.failing += 1
    checksByAsset.set(check.asset_id, entry)
  }

  const pipelineByDataset = new Map(
    pipelines
      .filter((pipeline) => pipeline.dataset)
      .map((pipeline) => [pipeline.dataset!.id, pipeline]),
  )

  // In-flight runs: ops grouped by their branch scope (pipeline-run-*), or
  // standalone when the run carries no scope.
  const inboundOps = operations.filter(
    (op) => op.status === 'running' || op.status === 'queued',
  )
  const inboundByScope = new Map<string, Array<ObservatoryOperation>>()
  for (const op of inboundOps) {
    const scope =
      typeof op.metadata.scope === 'string' ? op.metadata.scope : `op:${op.id}`
    inboundByScope.set(scope, [...(inboundByScope.get(scope) ?? []), op])
  }

  // Which datasets have in-flight work: a run targets the dataset when its
  // op.target is one of the dataset's source assets.
  const inboundByDataset = new Map<string, number>()
  for (const op of inboundOps) {
    if (!op.target) continue
    inboundByDataset.set(
      op.target.id,
      (inboundByDataset.get(op.target.id) ?? 0) + 1,
    )
  }

  const inbound: Array<LakeInbound> = [...inboundByScope.entries()]
    .map(([scope, ops]) => {
      const failed = ops.filter((op) => op.status === 'failed').length
      const running = ops.filter(
        (op) => op.status === 'running' || op.status === 'queued',
      ).length
      const interesting =
        ops.find((op) => op.status === 'failed') ??
        ops.find((op) => op.status === 'running') ??
        ops[0]
      const targets = [
        ...new Set(ops.map((op) => op.target?.label).filter(Boolean)),
      ] as Array<string>
      const started = ops
        .map((op) => op.started_at)
        .filter(Boolean)
        .sort()[0]
      const state: StatusState =
        failed > 0 ? 'error' : running > 0 ? 'ok' : 'unknown'
      return {
        failed,
        focusTarget: `op:${interesting.id}`,
        id: scope,
        label: scope.startsWith('op:')
          ? (interesting.name ?? 'run')
          : scopeLabel(scope),
        running,
        startedAt: started ?? null,
        state,
        targetExtra: Math.max(0, targets.length - 3),
        targets: targets.slice(0, 3),
        total: ops.length,
      }
    })
    .sort((a, b) => b.total - a.total || a.label.localeCompare(b.label))

  const cells: Array<LakeCell> = datasets.map((dataset) => {
    const sourceAsset = dataset.source_refs
      .map((ref) => assetById.get(ref.id))
      .find(Boolean)
    const group = sourceAsset?.group ?? 'datasets'
    const checks = checksByAsset.get(sourceAsset?.id ?? '') ?? {
      failing: 0,
      total: 0,
    }
    const pipeline = pipelineByDataset.get(dataset.id)
    const inboundCount = dataset.source_refs.reduce(
      (sum, ref) => sum + (inboundByDataset.get(ref.id) ?? 0),
      0,
    )
    const state = worst(
      dataset.readiness_state,
      checks.failing > 0 ? 'error' : 'ok',
      pipeline?.freshness_state ?? 'unknown',
    )
    return {
      checksFailing: checks.failing,
      checksTotal: checks.total,
      focusTarget: `dataset:${dataset.id}`,
      focused:
        (focus?.kind === 'dataset' && focus.id === dataset.id) ||
        (focus?.kind === 'asset' &&
          dataset.source_refs.some((ref) => ref.id === focus.id)),
      freshnessAt: pipeline?.freshness_at ?? null,
      group,
      id: dataset.id,
      inbound: inboundCount,
      name: dataset.name,
      owner: dataset.owner,
      publication: dataset.publication_state,
      readiness: dataset.readiness_state,
      state,
    }
  })

  const cellRank = (cell: LakeCell) =>
    (cell.state === 'error' ? 4 : 0) +
    (cell.state === 'warning' ? 2 : 0) +
    (cell.inbound > 0 ? 1 : 0)
  const byGroup = new Map<string, Array<LakeCell>>()
  for (const cell of cells) {
    byGroup.set(cell.group, [...(byGroup.get(cell.group) ?? []), cell])
  }

  const columns: Array<LakeColumn> = [...byGroup.entries()]
    .map(([group, groupCells]) => {
      const sorted = groupCells.sort(
        (a, b) => cellRank(b) - cellRank(a) || a.name.localeCompare(b.name),
      )
      return {
        cells: sorted.slice(0, CELL_CAP),
        count: sorted.length,
        error: sorted.filter((c) => c.state === 'error').length,
        id: group,
        label: columnLabel(group),
        warning: sorted.filter((c) => c.state === 'warning').length,
      }
    })
    .sort(
      (a, b) =>
        stageRank(a.id) - stageRank(b.id) || a.label.localeCompare(b.label),
    )

  return {
    columns,
    inbound,
    total: cells.length,
  }
}
