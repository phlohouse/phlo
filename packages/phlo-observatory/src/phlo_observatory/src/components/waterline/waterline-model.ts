/**
 * The waterline — the deck's situation display.
 *
 * One shared time axis across the center: each lane is a dataset, each
 * bar is an operation that touched it, positioned by when it started and
 * how long it ran. Running work extends to the right edge and pulses;
 * failures print red; a freshness tick marks when data last landed. The
 * shape of the last 24h — bursts, stalls, gaps — is the thing a list of
 * objects can never show.
 */

import type {
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
  /** 'dataset' lanes carry the landed tick; 'run' lanes are WAP branches. */
  kind: 'dataset' | 'run'
  focusTarget: string
  bars: Array<WaterlineBar>
  state: StatusState
  /** Most recent op end/start in the window — drives lane ordering. */
  lastActivity: number
  /** Fraction of the window where the landed tick sits, when present. */
  landedAtFrac?: number
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
  datasets,
  operations,
  pipelines,
  now = Date.now(),
}: {
  datasets: Array<ObservatoryDataset>
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

  const lanes: Array<WaterlineLane> = [...laneSeeds.entries()]
    .map(([key, seed]) => {
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
          ...bars.map((bar) =>
            new Date(bar.endedAt ?? bar.startedAt).getTime(),
          ),
          landedMs ?? 0,
        ),
        state: laneState(bars),
      }
    })
    .sort(
      (a, b) =>
        b.lastActivity - a.lastActivity || a.label.localeCompare(b.label),
    )

  return {
    laneOverflow: Math.max(0, lanes.length - LANE_CAP),
    lanes: lanes.slice(0, LANE_CAP),
    platformBars,
    windowEnd,
    windowStart,
  }
}
