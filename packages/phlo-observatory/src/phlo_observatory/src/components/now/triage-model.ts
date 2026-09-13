/**
 * Triage model for /now: turns failed operations, failing checks, stale
 * datasets, and degraded services into one ranked queue. Each item carries
 * a score (severity + blast radius + recency), a kind for filtering, a
 * state for color, evidence lines, and the deep link into the workbench
 * that owns it.
 */
import type {
  ObservatoryDatasetPipeline,
  ObservatoryLogEvent,
  ObservatoryOperation,
  ObservatoryQualityCheck,
  ObservatoryService,
} from '@/observatory/api/types'
import type { StatusState } from '@/components/observatory/status'

export type TriageKind = 'run' | 'check' | 'dataset' | 'service'

export interface TriageItem {
  id: string
  kind: TriageKind
  title: string
  state: StatusState
  /** Higher = triage earlier. */
  score: number
  reason: string
  /** Compact evidence fragments: scope, owner, age. */
  meta: string
  href: string
  /** Sort timestamp (newest first within equal scores). */
  at: string
  /** Pipeline actions available on this item's dataset, if any. */
  datasetId?: string
  /** ?focus= value that opens the inspector on this item's object. */
  focus?: string
}

const SEVERITY_SCORE: Record<string, number> = {
  critical: 40,
  high: 30,
  medium: 20,
  low: 10,
}

function ageBonus(timestamp?: string | null): number {
  if (!timestamp) return 0
  const hours = (Date.now() - new Date(timestamp).getTime()) / 3_600_000
  if (!Number.isFinite(hours) || hours < 0) return 0
  if (hours < 1) return 15
  if (hours < 24) return 8
  return 0
}

function textMetric(
  metadata: Record<string, unknown> | undefined,
  keys: Array<string>,
): string | null {
  for (const key of keys) {
    const value = metadata?.[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
}

/** 'pipeline-run-3a9cc…' → 'run 3a9cc318'. */
function shortScope(label: string): string {
  const runMatch = /^(?:pipeline-run|run)-([0-9a-f]{8})/i.exec(label)
  if (runMatch) return `run ${runMatch[1]}`
  return label
}

function operationItem(operation: ObservatoryOperation): TriageItem {
  const reason =
    textMetric(operation.metadata, [
      'exception_message',
      'failure_reason',
      'error',
      'reason',
      'message',
    ]) ??
    operation.health.message ??
    'Run failed.'
  const at = operation.completed_at ?? operation.started_at ?? ''
  const score =
    60 +
    (operation.kind.startsWith('pipeline') ? 15 : 0) +
    (operation.kind.includes('quality') ? 10 : 0) +
    ageBonus(at)
  return {
    id: `run:${operation.id}`,
    kind: 'run',
    title: operation.name,
    state: 'error',
    score,
    reason,
    meta: [
      operation.kind,
      operation.target?.label
        ? `scope: ${shortScope(operation.target.label)}`
        : null,
    ]
      .filter(Boolean)
      .join(' · '),
    href: `/operations?operationId=${encodeURIComponent(operation.id)}`,
    at,
    focus: `op:${operation.id}`,
  }
}

function checkItem(check: ObservatoryQualityCheck): TriageItem {
  const blocking = check.blocking && check.status !== 'passing'
  const score =
    (check.status === 'failing' ? 50 : 25) +
    (blocking ? 20 : 0) +
    (SEVERITY_SCORE[check.severity ?? ''] ?? 5)
  const dataset = textMetric(check.metadata, ['dataset'])
  return {
    id: `check:${check.id}`,
    kind: 'check',
    title: check.name,
    state: check.status === 'failing' ? 'error' : 'warning',
    score,
    reason: blocking
      ? 'Blocking check — holds release until resolved.'
      : check.status === 'failing'
        ? 'Check failing.'
        : 'Check reporting warnings.',
    meta: [
      `scope: ${dataset ?? check.asset_id}`,
      `owner: ${textMetric(check.metadata, ['owner']) ?? 'unassigned'}`,
      check.severity ?? check.status,
    ]
      .filter(Boolean)
      .join(' · '),
    href: `/quality?checkId=${encodeURIComponent(check.id)}`,
    at: '',
    datasetId: dataset ?? undefined,
    focus: `check:${check.id}`,
  }
}

function datasetItem(pipeline: ObservatoryDatasetPipeline): TriageItem | null {
  const dataset = pipeline.dataset
  if (!dataset) return null
  const state = pipeline.freshness_state
  if (state !== 'error' && state !== 'warning') return null
  const score = (state === 'error' ? 45 : 20) + ageBonus(pipeline.freshness_at)
  return {
    id: `dataset:${dataset.id}`,
    kind: 'dataset',
    title: dataset.name,
    state,
    score,
    reason:
      state === 'error'
        ? 'Dataset is stale beyond its freshness target.'
        : 'Dataset freshness is drifting toward its target.',
    meta: [
      `readiness: ${dataset.readiness_state}`,
      dataset.publication_state !== 'published'
        ? dataset.publication_state
        : null,
    ]
      .filter(Boolean)
      .join(' · '),
    href: `/datasets/${encodeURIComponent(dataset.id)}`,
    at: pipeline.freshness_at ?? '',
    datasetId: dataset.id,
    focus: `dataset:${dataset.id}`,
  }
}

function serviceItem(service: ObservatoryService): TriageItem | null {
  const configured =
    typeof service.in_stack === 'boolean'
      ? service.in_stack
      : service.definition_state === 'configured'
  if (!configured) return null
  const unhealthy =
    service.health.state === 'error' ||
    service.status === 'unhealthy' ||
    (service.status === 'stopped' && service.health.state !== 'ok')
  const degraded = service.health.state === 'warning'
  if (!unhealthy && !degraded) return null
  return {
    id: `service:${service.id}`,
    kind: 'service',
    title: service.name,
    state: unhealthy ? 'error' : 'warning',
    score: unhealthy ? 40 : 15,
    reason: unhealthy
      ? 'Service is down or unhealthy — dependent work may fail.'
      : 'Service is degraded.',
    meta: service.health.message ?? service.status,
    href: `/services?serviceId=${encodeURIComponent(service.id)}`,
    at: '',
    focus: `service:${service.id}`,
  }
}

export function buildTriageQueue({
  operations,
  quality,
  pipelines,
  services,
  logs,
}: {
  operations: Array<ObservatoryOperation>
  quality: Array<ObservatoryQualityCheck>
  pipelines: Array<ObservatoryDatasetPipeline>
  services: Array<ObservatoryService>
  logs: Array<ObservatoryLogEvent>
}): Array<TriageItem> {
  const items: Array<TriageItem> = [
    ...operations
      .filter((operation) => operation.status === 'failed')
      .map(operationItem),
    ...quality.filter((check) => check.status !== 'passing').map(checkItem),
    ...pipelines
      .map(datasetItem)
      .filter((item): item is TriageItem => item !== null),
    ...services
      .map(serviceItem)
      .filter((item): item is TriageItem => item !== null),
  ]

  // Error logs whose resource isn't already represented get a folded item.
  const covered = new Set(items.map((item) => item.id.split(':', 2)[1]))
  for (const log of logs) {
    if (log.level !== 'error' || !log.resource) continue
    if (covered.has(log.resource.id)) continue
    covered.add(log.resource.id)
    items.push({
      id: `log:${log.id}`,
      kind: 'run',
      title: log.message,
      state: 'error',
      score: 30 + ageBonus(log.timestamp),
      reason: 'Platform error event.',
      meta: [
        log.source,
        log.resource.label ? `scope: ${log.resource.label}` : null,
      ]
        .filter(Boolean)
        .join(' · '),
      href: `/logs?logId=${encodeURIComponent(log.id)}`,
      at: log.timestamp ?? '',
    })
  }

  return items.sort((left, right) => {
    if (left.score !== right.score) return right.score - left.score
    return right.at.localeCompare(left.at)
  })
}
