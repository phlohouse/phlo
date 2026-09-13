/**
 * The board's decision model — what the lakehouse needs from a human.
 *
 * Publishing readiness is the policy verdict per dataset: blockers that
 * forbid publication, warnings that don't, evidence still missing, and the
 * publish/retire transitions the operator may run. The board turns that
 * into a decision queue — ready rows carry executable actions, blocked
 * rows explain why the action is disabled. Nothing here is an inventory;
 * every row is a call someone has to make.
 */

import type {
  ObservatoryAction,
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryPublishingReadinessItem,
} from '@/observatory/api/types'

import type { StatusState } from '@/components/observatory/status'

export type DecisionKind = 'blocked' | 'ready' | 'warning' | 'evidence'

export interface BoardDecision {
  datasetId: string
  name: string
  owner?: string | null
  publication: string
  state: StatusState
  kind: DecisionKind
  policy: string
  blockers: Array<string>
  warnings: Array<string>
  missing: Array<string>
  /** Executable actions — ids scoped as `dataset:{id}:{action}`. */
  actions: Array<ObservatoryAction>
  focusTarget: string
}

export interface BoardCounts {
  blocked: number
  ready: number
  warning: number
  evidence: number
  /** Pipelines whose freshness is stale (error or warning). */
  stale: number
}

export interface BoardModel {
  decisions: Array<BoardDecision>
  /** Decisions before DECISION_CAP — the subbar's honest total. */
  total: number
  counts: BoardCounts
  /** Readiness items that failed to load or matched no dataset. */
  orphanCount: number
}

const DECISION_CAP = 40

const KIND_RANK: Record<DecisionKind, number> = {
  blocked: 0,
  ready: 1,
  warning: 2,
  evidence: 3,
}

function executableAction(
  datasetId: string,
  action: {
    id: string
    label: string
    enabled: boolean
    reason?: string | null
    consequences: Array<string>
  },
): ObservatoryAction {
  return {
    id: `dataset:${datasetId}:${action.id}`,
    label: action.label,
    kind: `dataset.${action.id}`,
    enabled: action.enabled,
    requires_confirmation: true,
    reason: action.reason ?? undefined,
    risk_level: 'medium',
    expected_evidence: action.consequences,
  }
}

function classify(
  item: ObservatoryPublishingReadinessItem,
): DecisionKind | null {
  const { publishing } = item
  const publishable = publishing.actions.some(
    (action) => action.id === 'publish' && action.enabled,
  )
  if (publishing.state === 'error' || publishing.blockers.length > 0) {
    return 'blocked'
  }
  if (publishable) return 'ready'
  if (publishing.state === 'warning' || publishing.warnings.length > 0) {
    return 'warning'
  }
  if (
    publishing.missing_evidence.length > 0 ||
    publishing.state === 'unknown'
  ) {
    return 'evidence'
  }
  return null
}

export function buildBoardModel({
  datasets,
  pipelines,
  publishing,
}: {
  datasets: Array<ObservatoryDataset>
  pipelines: Array<ObservatoryDatasetPipeline>
  publishing: Array<ObservatoryPublishingReadinessItem>
}): BoardModel {
  const datasetById = new Map(datasets.map((dataset) => [dataset.id, dataset]))

  const counts: BoardCounts = {
    blocked: 0,
    ready: 0,
    warning: 0,
    evidence: 0,
    stale: pipelines.filter(
      (pipeline) =>
        pipeline.freshness_state === 'error' ||
        pipeline.freshness_state === 'warning',
    ).length,
  }

  const decisions: Array<BoardDecision> = []
  let orphanCount = 0
  for (const item of publishing) {
    const kind = classify(item)
    if (!kind) continue
    const dataset = datasetById.get(item.dataset_id)
    if (!dataset) {
      orphanCount += 1
      continue
    }
    counts[kind] += 1
    decisions.push({
      actions: item.publishing.actions
        .filter(
          (action) =>
            action.enabled ||
            (action.id === 'publish' &&
              dataset.publication_state !== 'published'),
        )
        .map((action) => executableAction(item.dataset_id, action)),
      blockers: item.publishing.blockers,
      datasetId: item.dataset_id,
      focusTarget: `dataset:${item.dataset_id}`,
      kind,
      missing: item.publishing.missing_evidence,
      name: dataset.name,
      owner: dataset.owner,
      policy: item.publishing.policy_name,
      publication: dataset.publication_state,
      state: item.publishing.state,
      warnings: item.publishing.warnings,
    })
  }

  decisions.sort(
    (a, b) =>
      KIND_RANK[a.kind] - KIND_RANK[b.kind] || a.name.localeCompare(b.name),
  )

  return {
    counts,
    decisions: decisions.slice(0, DECISION_CAP),
    orphanCount,
    total: decisions.length,
  }
}
