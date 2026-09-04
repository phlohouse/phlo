import { describe, expect, it } from 'vitest'

import {
  buildDatasetTransitionPlan,
  classifyDatasetTransitionResult,
  datasetTransitionActionId,
  parseCanonicalDatasetProjection,
  profileProjection,
} from './datasetProjection'
import type { CanonicalDatasetProjection } from './types'

function projectionFixture(): CanonicalDatasetProjection {
  return {
    dataset_id: 'gold.orders',
    table_id: 'gold.orders',
    candidate: false,
    owner: 'analytics',
    classifications: ['internal'],
    workflow_state: 'draft',
    publication_state: 'draft',
    approval_state: null,
    policy_version: 'governance-surface-v1',
    last_action_id: 'seed-promotion',
    declared: true,
    controls: [
      { control: 'owner_recorded', status: 'passed' },
      { control: 'quality_checks_passed', status: 'failed' },
    ],
    evidence: [
      {
        kind: 'ownership',
        subject: 'gold.orders',
        status: 'passed',
        source: 'governance surface',
      },
    ],
    readiness: {
      action: 'publish',
      ready: false,
      policy_version: 'governance-surface-v1',
      reasons: [
        'Blocking quality checks must pass before the transition.',
        'Dataset has no declared classification.',
      ],
      blockers: [
        {
          control: 'quality_checks_passed',
          severity: 'blocker',
          message: 'Blocking quality checks must pass before the transition.',
          evidence_kind: 'quality_checks',
        },
      ],
      warnings: [
        {
          control: 'classification_declared',
          severity: 'warning',
          message: 'Dataset has no declared classification.',
          evidence_kind: 'classification',
        },
      ],
      missing_evidence: [],
    },
    allowed_transitions: [],
    record: {
      dataset_id: 'gold.orders',
      table_id: 'gold.orders',
      publication_state: 'draft',
      owner: 'analytics',
      last_action_id: 'seed-promotion',
    },
  }
}

describe('parseCanonicalDatasetProjection', () => {
  it('accepts a well-formed projection payload', () => {
    const parsed = parseCanonicalDatasetProjection(projectionFixture())
    expect(parsed?.dataset_id).toBe('gold.orders')
    expect(parsed?.readiness.reasons).toHaveLength(2)
  })

  it('rejects malformed payloads instead of inventing facts', () => {
    expect(parseCanonicalDatasetProjection(null)).toBeNull()
    expect(parseCanonicalDatasetProjection('projection')).toBeNull()
    expect(parseCanonicalDatasetProjection({})).toBeNull()
    expect(
      parseCanonicalDatasetProjection({ dataset_id: 'x', readiness: null }),
    ).toBeNull()
    expect(
      parseCanonicalDatasetProjection({
        dataset_id: 'x',
        table_id: 'x',
        readiness: { reasons: 'nope' },
      }),
    ).toBeNull()
  })

  it('extracts the projection from a profile read model', () => {
    const profile = {
      canonical: projectionFixture(),
    } as never
    expect(profileProjection(profile)?.table_id).toBe('gold.orders')
    expect(profileProjection({ canonical: null } as never)).toBeNull()
  })
})

describe('buildDatasetTransitionPlan', () => {
  it('explains the exact observed state and ordered canonical reasons', () => {
    const plan = buildDatasetTransitionPlan(projectionFixture(), 'publish')
    expect(plan.actionId).toBe('dataset:gold.orders:publish')
    expect(plan.expectedState).toBe('draft')
    expect(plan.allowed).toBe(false)
    expect(plan.ready).toBe(false)
    expect(plan.policyVersion).toBe('governance-surface-v1')
    expect(plan.reasons).toEqual([
      'Blocking quality checks must pass before the transition.',
      'Dataset has no declared classification.',
    ])
  })

  it('keys each action by the canonical action id', () => {
    expect(datasetTransitionActionId('gold.orders', 'retire')).toBe(
      'dataset:gold.orders:retire',
    )
  })
})

describe('classifyDatasetTransitionResult', () => {
  const result = (
    status: 'succeeded' | 'failed' | 'skipped',
    message: string,
  ) =>
    ({
      action: { id: 'dataset:gold.orders:publish' },
      status,
      message,
      operation: null,
    }) as never

  it('treats a plain success as committed and durable', () => {
    const verdict = classifyDatasetTransitionResult(
      result('succeeded', "'publish' committed for gold.orders."),
    )
    expect(verdict).toMatchObject({ outcome: 'committed', durable: true })
  })

  it('recognizes replayed durability instead of a new success', () => {
    const verdict = classifyDatasetTransitionResult(
      result(
        'succeeded',
        "'publish' already committed; replaying outcome. (replayed)",
      ),
    )
    expect(verdict).toMatchObject({ outcome: 'replayed', durable: true })
  })

  it('recognizes idempotent reporting of the existing state', () => {
    const verdict = classifyDatasetTransitionResult(
      result('succeeded', 'gold.orders is already published. (idempotent)'),
    )
    expect(verdict).toMatchObject({ outcome: 'idempotent', durable: true })
  })

  it('never turns a blocked result into success', () => {
    const verdict = classifyDatasetTransitionResult(
      result(
        'skipped',
        "Policy blocked 'publish': Blocking quality checks must pass before the transition.",
      ),
    )
    expect(verdict).toMatchObject({ outcome: 'blocked', durable: false })
  })

  it('maps failed results to conflict without durability', () => {
    const verdict = classifyDatasetTransitionResult(
      result(
        'failed',
        "Expected state 'draft' for gold.orders, found 'published'.",
      ),
    )
    expect(verdict).toMatchObject({ outcome: 'conflict', durable: false })
  })
})
