/**
 * Source-level regression tests: the Observatory
 * Dataset surfaces render one canonical projection from phlo-api and keep no
 * local eligibility inference, no second canonical store, and no optimistic
 * transition success.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const routesDir = resolve(import.meta.dirname, '../../routes')

const readRoute = (name: string) =>
  readFileSync(resolve(routesDir, name), 'utf8')

describe('Catalog dataset route purity', () => {
  const route = readRoute('datasets.tsx')

  it('loads canonical readiness from the bulk phlo-api endpoint', () => {
    expect(route).toContain('getObservatoryPublishingReadinessDirect()')
  })

  it('keeps no owner or classification eligibility inference', () => {
    expect(route).not.toContain('datasetQueueReason(dataset)')
    expect(route).not.toContain("'Ownership is missing.'")
    expect(route).not.toContain("'Classification is missing.'")
    expect(route).not.toContain("'assign owner'")
    expect(route).not.toContain("'declare classification'")
  })
})

describe('Publishing route purity and explain-then-execute', () => {
  const route = readRoute('publishing.tsx')

  it('loads promoted dataset readiness with one bulk request', () => {
    expect(route).toContain('getObservatoryPublishingReadinessDirect()')
    expect(route).not.toContain('getObservatoryDatasetProfileDirect')
    expect(route).not.toContain('Promise.all(')
  })

  it('keeps no locally inferred blockers, approvals, or next actions', () => {
    expect(route).not.toContain("'owner missing'")
    expect(route).not.toContain("'classification missing'")
    expect(route).not.toContain("'quality blocking'")
    expect(route).not.toContain('publicationReadiness(')
  })

  it('explains transitions before executing them', () => {
    expect(route).toContain('Explain before execute')
    expect(route).toContain('Exact version')
    expect(route).toContain('canonical reason')
  })

  it('executes transitions against the exact observed version', () => {
    expect(route).toContain('runObservatoryActionDirect({')
    expect(route).toContain('expectedState')
    expect(route).toContain('dataset.publication_state')
  })

  it('reloads durable state after every transition result', () => {
    expect(route).toContain('reloadDurableState')
    expect(route).toContain('classifyDatasetTransitionResult')
  })
})

describe('Governance route purity', () => {
  const route = readRoute('governance.tsx')

  it('derives next actions from server control verdicts only', () => {
    const nextAction = route.slice(
      route.indexOf('function governanceNextAction'),
      route.indexOf('function controlById'),
    )
    expect(nextAction).not.toContain('row.owner')
    expect(nextAction).not.toContain('row.classifications')
  })
})

describe('Dataset profile route purity', () => {
  const route = readRoute('datasets.$datasetId.tsx')

  it('renders the shared canonical projection panel', () => {
    expect(route).toContain('DatasetProjectionPanel')
    expect(route).toContain('profileProjection(')
  })

  it('derives blockers from the canonical verdict, not dataset fields', () => {
    const blocker = route.slice(
      route.indexOf('function datasetBlocker'),
      route.indexOf('function datasetNextAction'),
    )
    expect(blocker).toContain('profile.publishing.blockers[0]')
    expect(blocker).not.toContain('Owner missing')
    expect(blocker).not.toContain('Classification missing')
    expect(blocker).not.toContain('profile.quality.find')
    expect(blocker).not.toContain('profile.dataset.owner')
  })
})
