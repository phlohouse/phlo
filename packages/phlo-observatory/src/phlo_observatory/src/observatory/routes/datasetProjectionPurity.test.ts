/** Dataset screens must not invent governed data while v1 lacks a contract. */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const routesDir = resolve(import.meta.dirname, '../../routes')

const readRoute = (name: string) =>
  readFileSync(resolve(routesDir, name), 'utf8')

describe('Replacement Dataset list route', () => {
  const route = readRoute('datasets.tsx')

  it('shows a contract gap rather than using legacy Dataset reads or assets', () => {
    expect(route).toContain("datasetV1Unavailable('list')")
    expect(route).toContain('not shown here as substitutes')
    expect(route).not.toContain('getObservatoryDataset')
    expect(route).not.toContain('runObservatoryAction')
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

describe('Replacement Dataset profile route', () => {
  const route = readRoute('datasets.$datasetId.tsx')

  it('preserves the requested identifier without inventing a profile', () => {
    expect(route).toContain('title={datasetId}')
    expect(route).toContain("datasetV1Unavailable('detail')")
    expect(route).toContain('use Dagster asset data as Dataset evidence')
    expect(route).not.toContain('getObservatoryDatasetProfile')
    expect(route).not.toContain('runObservatoryAction')
  })
})
