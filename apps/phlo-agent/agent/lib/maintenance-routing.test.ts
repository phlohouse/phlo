/** Tests for confidence-gated, deterministic maintenance routing. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { Experimental_EvaluationMockModelV4 as MockEvaluationModel } from 'ai/test'
import {
  routeMaintenanceFindings,
  type MaintenanceFinding,
} from './maintenance-routing.ts'

type ModelResult = Awaited<ReturnType<MockEvaluationModel['doEvaluate']>>

const fixable: MaintenanceFinding = {
  id: 'GHSA-example',
  packageName: 'example',
  installedVersion: '1.2.3',
  fixedVersions: ['1.2.4'],
  dependencyScope: 'runtime',
  advisorySummary: 'A parser vulnerability is fixed by an ordinary patch release.',
  affectedManifests: ['pyproject.toml'],
}

function mockJev(answers: ModelResult['answers'], confidence?: Record<string, number>) {
  return new MockEvaluationModel({
    doEvaluate: async () => ({
      answers,
      warnings: [],
      ...(confidence === undefined ? {} : {
        providerMetadata: { typesafe: { confidence } },
      }),
    }),
  })
}

test('routes a clear fixable finding to the selected lane', async () => {
  const result = await routeMaintenanceFindings([fixable], mockJev({
    route_0: {
      type: 'choice',
      choice: 'routine_update',
      probabilities: {
        routine_update: 0.96,
        compatibility_review: 0.03,
        security_review: 0.01,
      },
    },
  }, { route_0: 0.92 }))

  assert.deepEqual(result.decisions, [{
    id: 'GHSA-example',
    route: 'routine_update',
    suggestedRoute: 'routine_update',
    confidence: 0.92,
    reason: 'classified',
  }])
})

test('routes uncertain findings to a human while preserving the suggestion', async () => {
  const result = await routeMaintenanceFindings([fixable], mockJev({
    route_0: {
      type: 'choice',
      choice: 'compatibility_review',
      probabilities: {
        routine_update: 0.2,
        compatibility_review: 0.55,
        security_review: 0.25,
      },
    },
  }, { route_0: 0.79 }))

  assert.deepEqual(result.decisions[0], {
    id: 'GHSA-example',
    route: 'human_review',
    suggestedRoute: 'compatibility_review',
    confidence: 0.79,
    reason: 'missing-or-low-confidence',
  })
})

test('batches fixable findings into one zero-data-retention request', async () => {
  let calls = 0
  const model = new MockEvaluationModel({
    doEvaluate: async (options) => {
      calls += 1
      assert.deepEqual(Object.keys(options.questions), ['route_0', 'route_2'])
      assert.equal(options.providerOptions?.gateway?.zeroDataRetention, true)
      assert.deepEqual(Object.keys((options.state as { findings: object }).findings), [
        'route_0',
        'route_2',
      ])
      return {
        answers: {
          route_0: {
            type: 'choice',
            choice: 'routine_update',
            probabilities: {
              routine_update: 0.9,
              compatibility_review: 0.08,
              security_review: 0.02,
            },
          },
          route_2: {
            type: 'choice',
            choice: 'security_review',
            probabilities: {
              routine_update: 0.01,
              compatibility_review: 0.04,
              security_review: 0.95,
            },
          },
        },
        warnings: [],
        providerMetadata: {
          typesafe: { confidence: { route_0: 0.9, route_2: 0.95 } },
        },
      }
    },
  })

  const result = await routeMaintenanceFindings([
    fixable,
    { ...fixable, id: 'GHSA-no-fix', fixedVersions: [] },
    { ...fixable, id: 'GHSA-review' },
  ], model)

  assert.equal(calls, 1)
  assert.deepEqual(result.decisions.map(({ route }) => route), [
    'routine_update',
    'no_fix_available',
    'security_review',
  ])
})

test('does not call Jev when no fixed version is listed', async () => {
  let called = false
  const model = new MockEvaluationModel({
    doEvaluate: async () => {
      called = true
      return { answers: {}, warnings: [] }
    },
  })
  const result = await routeMaintenanceFindings([{ ...fixable, fixedVersions: [] }], model)

  assert.equal(called, false)
  assert.deepEqual(result.decisions, [{
    id: 'GHSA-example',
    route: 'no_fix_available',
    confidence: 1,
    reason: 'no-fix-listed',
  }])
})

test('requires confidence metadata before selecting an automatic lane', async () => {
  const result = await routeMaintenanceFindings([fixable], mockJev({
    route_0: {
      type: 'choice',
      choice: 'routine_update',
      probabilities: {
        routine_update: 1,
        compatibility_review: 0,
        security_review: 0,
      },
    },
  }))

  assert.equal(result.decisions[0]?.route, 'human_review')
  assert.equal(result.decisions[0]?.confidence, null)
})
