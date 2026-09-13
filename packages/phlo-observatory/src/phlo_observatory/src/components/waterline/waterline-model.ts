/**
 * The waterline — the deck's situation display.
 *
 * One shared time axis across the center: each lane is a dataset, each
 * bar is an operation that touched it, positioned by when it started and
 * how long it ran. Running work extends to the right edge and pulses;
 * failures print red; a freshness tick marks when data last landed. The
 * shape of the last 24h — bursts, stalls, gaps — is the thing a list of
 * objects can never show.
 *
 * At scale the lanes aggregate: when more datasets are active than a
 * screen can hold, each lane becomes a lake layer carrying every member
 * dataset's ops as an overlaid density strip — you read concentration
 * and failures, not individuals. Expanding a layer (or focusing a
 * dataset inside it) fans that lane back out into member lanes.
 */

import type {
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryOperation,
} from '@/observatory/api/types'

import type { StatusState } from '@/components/observatory/status'

export interface WaterlineBar {
  id: string
  /** Position within the window, 0..1. */
  left: number
  /** Width within the window, 0..1 (min-clamped). */
  width: number
  status: ObservatoryOperation['status']
  label: string
  startedAt: string
  endedAt?: string | null
  durationMs: number
  focusTarget: string
  /** True when the op is a publication transition — renders as a tick. */
  publish: boolean
}

export interface WaterlineLane {
  id: string
  label: string
  /**
   * 'dataset' lanes carry the landed tick; 'run' lanes are WAP branches;
   * 'group' lanes are lake layers aggregating many datasets at scale.
   */
  kind: 'dataset' | 'run' | 'group'
  focusTarget: string
  bars: Array<WaterlineBar>
  state: StatusState
  /** Most recent op end/start in the window — drives lane ordering. */
  lastActivity: number
  /** Fraction of the window where the landed tick sits, when present. */
  landedAtFrac?: number
  /** Group lanes only: the layer key and how many datasets folded in. */
  group?: string
  memberCount?: number
  /** True when this group lane is expanded — member lanes follow it. */
  expanded?: boolean
  /** Members hidden beyond the member cap on an expanded group lane. */
  hiddenMembers?: number
  /** True on dataset lanes emitted under an expanded group header. */
  member?: boolean
}

export interface WaterlineModel {
  windowStart: number
  windowEnd: number
  lanes: Array<WaterlineLane>
  /** Lanes with window activity beyond LANE_CAP. */
  laneOverflow: number
  /** Ops that mapped to nothing — folded into the platform lane. */
  platformBars: Array<WaterlineBar>
}

const WINDOW_MS = 24 * 3_600_000
const LANE_CAP = 28
/** More active dataset lanes than this fold into layer lanes. */
const GROUP_AT = 12
/** Member lanes shown under an expanded group header. */
const MEMBER_CAP = 24
const MIN_WIDTH = 0.004

function frac(at: number, start: number, end: number): number {
  return Math.min(1, Math.max(0, (at - start) / (end - start)))
}

function barFor(
  op: ObservatoryOperation,
  start: number,
  end: number,
): WaterlineBar | null {
  const startedAt = op.started_at ?? op.completed_at
  if (!startedAt) return null
  const t0 = new Date(startedAt).getTime()
  const t1 = op.completed_at
    ? new Date(op.completed_at).getTime()
    : op.status === 'running' || op.status === 'queued'
      ? end
      : t0
  // Keep ops that overlap the window — a run started before it still
  // shows as a bar clamped to the left edge.
  if (!Number.isFinite(t0) || t0 > end || t1 < start) return null
  const left = frac(t0, start, end)
  const right = frac(Math.max(t1, t0), start, end)
  return {
    durationMs: Math.max(0, t1 - t0),
    endedAt: op.completed_at ?? null,
    focusTarget: `op:${op.id}`,
    id: op.id,
    label: op.name,
    left,
    publish:
      op.kind.includes('publish') ||
      op.kind.includes('retire') ||
      op.name.toLowerCase().includes('publish'),
    startedAt,
    status: op.status,
    width: Math.max(MIN_WIDTH, right - left),
  }
}

function laneState(bars: Array<WaterlineBar>): StatusState {
  if (bars.some((bar) => bar.status === 'failed')) return 'error'
  if (bars.some((bar) => bar.status === 'running')) return 'info'
  if (bars.length > 0) return 'ok'
  return 'unknown'
}

/** 'pipeline-run-3a9cc318…' → 'run 3a9cc318'. */
function runLabel(raw: string): string {
  const match = /^(?:pipeline-run|run)-?([0-9a-f]{8})/i.exec(raw)
  if (match) return `run ${match[1]}`
  if (/^[0-9a-f]{12,}$/i.test(raw)) return `run ${raw.slice(0, 8)}`
  return raw
}

export function buildWaterlineModel({
  assets,
  datasets,
  expanded,
  focus,
  operations,
  pipelines,
  now = Date.now(),
}: {
  assets: Array<ObservatoryAsset>
  datasets: Array<ObservatoryDataset>
  /** Layer key expanded into member dataset lanes (`grp:<group>`). */
  expanded?: string | null
  focus?: { kind: string; id: string } | null
  operations: Array<ObservatoryOperation>
  pipelines: Array<ObservatoryDatasetPipeline>
  now?: number
}): WaterlineModel {
  const windowEnd = now + WINDOW_MS * 0.04
  const windowStart = now - WINDOW_MS

  // Lane routing for an op: the dataset its target feeds, else the run
  // (WAP branch / scope) it belongs to, else the platform lane.
  const datasetByAsset = new Map<string, ObservatoryDataset>()
  for (const dataset of datasets) {
    for (const ref of dataset.source_refs) {
      datasetByAsset.set(ref.id, dataset)
    }
  }
  const assetById = new Map(assets.map((asset) => [asset.id, asset]))
  const groupOfDataset = (dataset: ObservatoryDataset): string => {
    const source = dataset.source_refs
      .map((ref) => assetById.get(ref.id))
      .find(Boolean)
    return source?.group ?? 'datasets'
  }
  const datasetById = new Map(datasets.map((d) => [d.id, d]))
  const landedByDataset = new Map(
    pipelines
      .filter((pipeline) => pipeline.dataset && pipeline.freshness_at)
      .map((pipeline) => [pipeline.dataset!.id, pipeline.freshness_at!]),
  )

  interface LaneSeed {
    label: string
    focusTarget: string
    kind: 'dataset' | 'run'
    bars: Array<WaterlineBar>
  }
  const laneSeeds = new Map<string, LaneSeed>()
  const platformBars: Array<WaterlineBar> = []

  for (const op of operations) {
    const bar = barFor(op, windowStart, windowEnd)
    if (!bar) continue
    const dataset = op.target
      ? (datasetByAsset.get(op.target.id) ?? datasetById.get(op.target.id))
      : undefined
    if (dataset) {
      const key = `dataset:${dataset.id}`
      const seed = laneSeeds.get(key) ?? {
        bars: [],
        focusTarget: key,
        kind: 'dataset' as const,
        label: dataset.name,
      }
      seed.bars.push(bar)
      laneSeeds.set(key, seed)
      continue
    }
    const scope =
      typeof op.metadata.scope === 'string' && op.metadata.scope
        ? op.metadata.scope
        : op.target?.kind === 'branch'
          ? op.target.id
          : typeof op.metadata.run_id === 'string'
            ? op.metadata.run_id
            : null
    if (scope) {
      const key = `run:${scope}`
      const seed = laneSeeds.get(key) ?? {
        bars: [],
        focusTarget: bar.focusTarget,
        kind: 'run' as const,
        label: runLabel(scope),
      }
      seed.bars.push(bar)
      laneSeeds.set(key, seed)
      continue
    }
    platformBars.push(bar)
  }

  // Datasets with a landed tick but no bars still earn a lane.
  for (const dataset of datasets) {
    const key = `dataset:${dataset.id}`
    if (!laneSeeds.has(key) && landedByDataset.has(dataset.id)) {
      laneSeeds.set(key, {
        bars: [],
        focusTarget: key,
        kind: 'dataset',
        label: dataset.name,
      })
    }
  }

  // Scale pass: too many dataset lanes fold into one lane per lake
  // layer. The expanded layer — from `?in=grp:` or from focusing a
  // dataset — fans back out into member lanes.
  const datasetSeeds = [...laneSeeds.values()].filter(
    (seed) => seed.kind === 'dataset',
  )
  const expandedGroups = new Set<string>()
  if (expanded) expandedGroups.add(expanded)
  const focusedDataset =
    focus?.kind === 'dataset'
      ? datasetById.get(focus.id)
      : focus?.kind === 'asset'
        ? datasetByAsset.get(focus.id)
        : undefined
  if (focusedDataset) expandedGroups.add(groupOfDataset(focusedDataset))

  const toLane = (key: string, seed: LaneSeed): WaterlineLane => {
    const bars = seed.bars.sort((a, b) => a.left - b.left)
    const datasetId = seed.kind === 'dataset' ? key.slice(8) : null
    const landedAt = datasetId ? landedByDataset.get(datasetId) : null
    const landedMs = landedAt ? new Date(landedAt).getTime() : null
    return {
      bars,
      focusTarget: seed.focusTarget,
      id: key,
      kind: seed.kind,
      label: seed.label,
      landedAtFrac:
        landedMs && landedMs >= windowStart
          ? frac(landedMs, windowStart, windowEnd)
          : undefined,
      lastActivity: Math.max(
        ...bars.map((bar) => new Date(bar.endedAt ?? bar.startedAt).getTime()),
        landedMs ?? 0,
      ),
      state: laneState(bars),
    }
  }

  const grouping = datasetSeeds.length > GROUP_AT
  const grouped = new Map<
    string,
    {
      bars: Array<WaterlineBar>
      landedMs: number
      memberLanes: Array<WaterlineLane>
    }
  >()
  const topLanes: Array<WaterlineLane> = []
  for (const [key, seed] of laneSeeds) {
    if (seed.kind !== 'dataset' || !grouping) {
      topLanes.push(toLane(key, seed))
      continue
    }
    const dataset = datasetById.get(key.slice(8))
    const group = dataset ? groupOfDataset(dataset) : 'datasets'
    const entry = grouped.get(group) ?? {
      bars: [],
      landedMs: 0,
      memberLanes: [],
    }
    entry.bars.push(...seed.bars)
    const landedAt = landedByDataset.get(key.slice(8))
    if (landedAt) {
      entry.landedMs = Math.max(entry.landedMs, new Date(landedAt).getTime())
    }
    entry.memberLanes.push({ ...toLane(key, seed), member: true })
    grouped.set(group, entry)
  }
  for (const [group, entry] of grouped) {
    const bars = entry.bars.sort((a, b) => a.left - b.left)
    entry.memberLanes.sort(
      (a, b) =>
        b.lastActivity - a.lastActivity || a.label.localeCompare(b.label),
    )
    const expanded = expandedGroups.has(group)
    topLanes.push({
      bars,
      expanded,
      focusTarget: `group:${group}`,
      group,
      hiddenMembers: expanded
        ? Math.max(0, entry.memberLanes.length - MEMBER_CAP)
        : 0,
      id: `group:${group}`,
      kind: 'group',
      label: group.replace(/[-_]+/g, ' '),
      landedAtFrac:
        entry.landedMs >= windowStart
          ? frac(entry.landedMs, windowStart, windowEnd)
          : undefined,
      lastActivity: Math.max(
        ...bars.map((bar) => new Date(bar.endedAt ?? bar.startedAt).getTime()),
        entry.landedMs,
      ),
      memberCount: entry.memberLanes.length,
      state: laneState(bars),
    })
    entry.memberLanes = expanded ? entry.memberLanes.slice(0, MEMBER_CAP) : []
    grouped.set(group, entry)
  }

  topLanes.sort(
    (a, b) => b.lastActivity - a.lastActivity || a.label.localeCompare(b.label),
  )

  // Expanded groups keep their header lane and drop member lanes in
  // directly beneath it — the header stays as the collapse affordance.
  const lanes: Array<WaterlineLane> = []
  for (const lane of topLanes.slice(0, LANE_CAP)) {
    lanes.push(lane)
    if (lane.kind === 'group' && lane.expanded && lane.group) {
      lanes.push(...(grouped.get(lane.group)?.memberLanes ?? []))
    }
  }

  return {
    laneOverflow: Math.max(0, topLanes.length - LANE_CAP),
    lanes,
    platformBars,
    windowEnd,
    windowStart,
  }
}
