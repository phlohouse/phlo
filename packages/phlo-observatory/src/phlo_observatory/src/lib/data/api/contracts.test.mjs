/** Checks actual client methods/parsers against freshly generated FastAPI OpenAPI. */
import { execFileSync } from 'node:child_process'
import { describe, expect, it } from 'vitest'
import {
  checkContract,
  discoverContracts,
} from '../../../../scripts/client-contracts.mjs'

const document = JSON.parse(
  execFileSync(
    'uv',
    [
      'run',
      '--locked',
      'python',
      '-c',
      'import json; from phlo_api.main import app; print(json.dumps(app.openapi()))',
    ],
    { cwd: '../../../..', encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 },
  ),
)
const contracts = discoverContracts()

describe('API/client method and response drift', () => {
  it('checks every discovered production phloApi call against real OpenAPI', () => {
    expect(contracts.length).toBeGreaterThan(60)
    const failures = []
    for (const contract of contracts) {
      try {
        checkContract(document, contract)
      } catch (error) {
        failures.push(String(error))
      }
    }
    expect(failures).toEqual([])
  })

  it('rejects an incompatible method and a renamed required API field', () => {
    const overview = contracts.find((c) => c.path === '/api/v1/overview')
    expect(overview).toBeDefined()
    const wrongMethod = structuredClone(document)
    wrongMethod.paths['/api/v1/overview'].post =
      wrongMethod.paths['/api/v1/overview'].get
    delete wrongMethod.paths['/api/v1/overview'].get
    expect(() => checkContract(wrongMethod, overview)).toThrow(
      'API method missing GET',
    )
    const wrongField = structuredClone(document)
    const model = wrongField.components.schemas.OverviewResponse
    model.properties.assets_total = model.properties.asset_count
    delete model.properties.asset_count
    model.required = model.required.map((key) =>
      key === 'asset_count' ? 'assets_total' : key,
    )
    expect(() => checkContract(wrongField, overview)).toThrow(
      'API no longer requires asset_count',
    )
  })
})
