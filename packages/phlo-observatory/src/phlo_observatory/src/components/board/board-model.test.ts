/**
 * Board-model coverage: the publish-readiness verdicts become a decision
 * queue — ready rows carry executable actions, blocked rows explain why
 * they can't move, healthy published datasets stay off the board.
 */
import { describe, expect, it } from 'vitest'

import { buildBoardModel } from './board-model'
import type {
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryPublishingReadinessItem,
} from '@/observatory/api/types'

function dataset(
  id: string,
  publication: ObservatoryDataset['publication_state'] = 'draft',
): ObservatoryDataset {
  return {
    candidate: false,
    classifications: [],
    id,
    kinds: [],
    metadata: {},
    name: id,
    owner: 'data-eng',
    publication_state: publication,
    readiness_state: 'ok',
    source_refs: [],
  }
}

function readiness(
  dataset_id: string,
  publishing: Partial<ObservatoryPublishingReadinessItem['publishing']>,
): ObservatoryPublishingReadinessItem {
  return {
    dataset_id,
    publishing: {
      actions: [
        {
          consequences: ['Sets the Dataset publication state to published.'],
          enabled: false,
          id: 'publish',
          label: 'Publish internally',
          reason: 'Readiness policy has blockers.',
        },
        {
          consequences: ['Sets the Dataset publication state to retired.'],
          enabled: false,
          id: 'retire',
          label: 'Retire',
          reason: 'Only published Datasets can be retired.',
        },
      ],
      blockers: [],
      internal_only: true,
      missing_evidence: [],
      policy_name: 'default',
      state: 'ok',
      warnings: [],
      ...publishing,
    },
  }
}

function pipeline(
  id: string,
  freshness_state: ObservatoryDatasetPipeline['freshness_state'],
): ObservatoryDatasetPipeline {
  return {
    actions: [],
    dataset: dataset(id, 'published'),
    freshness_at: null,
    freshness_state,
    last_run: null,
    stages: [],
  }
}

describe('buildBoardModel', () => {
  it('classifies a publishable dataset as ready with an executable publish action', () => {
    const model = buildBoardModel({
      datasets: [dataset('orders_daily')],
      pipelines: [],
      publishing: [
        readiness('orders_daily', {
          actions: [
            {
              consequences: [],
              enabled: true,
              id: 'publish',
              label: 'Publish internally',
            },
          ],
        }),
      ],
    })
    expect(model.decisions).toHaveLength(1)
    expect(model.decisions[0].kind).toBe('ready')
    expect(model.decisions[0].actions[0].id).toBe(
      'dataset:orders_daily:publish',
    )
    expect(model.decisions[0].actions[0].enabled).toBe(true)
    expect(model.counts.ready).toBe(1)
  })

  it('orders blocked before ready, and carries the blocker text', () => {
    const model = buildBoardModel({
      datasets: [dataset('a_ready'), dataset('b_blocked')],
      pipelines: [],
      publishing: [
        readiness('a_ready', {
          actions: [
            {
              consequences: [],
              enabled: true,
              id: 'publish',
              label: 'Publish internally',
            },
          ],
        }),
        readiness('b_blocked', {
          blockers: ['freshness gate rejected the last run'],
          state: 'error',
        }),
      ],
    })
    expect(model.decisions.map((d) => d.datasetId)).toEqual([
      'b_blocked',
      'a_ready',
    ])
    expect(model.decisions[0].blockers).toEqual([
      'freshness gate rejected the last run',
    ])
    expect(model.counts.blocked).toBe(1)
  })

  it('keeps healthy published datasets off the board', () => {
    const model = buildBoardModel({
      datasets: [dataset('quiet', 'published')],
      pipelines: [],
      publishing: [readiness('quiet', { state: 'ok' })],
    })
    expect(model.decisions).toHaveLength(0)
  })

  it('counts stale pipelines separately from the decision queue', () => {
    const model = buildBoardModel({
      datasets: [],
      pipelines: [pipeline('a', 'error'), pipeline('b', 'ok')],
      publishing: [],
    })
    expect(model.counts.stale).toBe(1)
    expect(model.decisions).toHaveLength(0)
  })

  it('marks readiness rows whose dataset is gone as orphans', () => {
    const model = buildBoardModel({
      datasets: [],
      pipelines: [],
      publishing: [readiness('ghost', { blockers: ['gone'], state: 'error' })],
    })
    expect(model.orphanCount).toBe(1)
    expect(model.decisions).toHaveLength(0)
  })

  it('classifies missing-evidence datasets as evidence, not blocked', () => {
    const model = buildBoardModel({
      datasets: [dataset('pending')],
      pipelines: [],
      publishing: [
        readiness('pending', {
          missing_evidence: ['no owner sign-off recorded'],
          state: 'unknown',
        }),
      ],
    })
    expect(model.decisions[0].kind).toBe('evidence')
    expect(model.counts.evidence).toBe(1)
  })
})
