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
    const defaultOnly = structuredClone(document)
    const optionalCount = defaultOnly.components.schemas.OverviewResponse
    optionalCount.properties.asset_count.default = 0
    optionalCount.required = optionalCount.required.filter(
      (key) => key !== 'asset_count',
    )
    expect(() => checkContract(defaultOnly, overview)).toThrow(
      'API no longer requires asset_count',
    )
  })

  it('rejects required-field drift inside production JSON-text transforms', () => {
    const createIncident = contracts.find(
      (c) => c.path === '/api/v1/incidents' && c.method === 'post',
    )
    expect(createIncident).toBeDefined()
    // Execute the actual production transform, including both rejection paths.
    expect(createIncident.parser.safeParse('{').success).toBe(false)
    expect(createIncident.parser.safeParse('{}').success).toBe(false)
    const wrongField = structuredClone(document)
    const model = wrongField.components.schemas.IncidentView
    model.properties.incident_id = model.properties.id
    delete model.properties.id
    model.required = model.required.map((key) =>
      key === 'id' ? 'incident_id' : key,
    )
    expect(() => checkContract(wrongField, createIncident)).toThrow(
      'API no longer requires id',
    )
  })

  it('rejects incompatible CSV, NDJSON, and no-content declarations', () => {
    const textContracts = contracts.filter((c) => c.text)
    expect(textContracts.length).toBeGreaterThanOrEqual(3)
    for (const contract of textContracts) {
      const changed = structuredClone(document)
      const path = Object.keys(changed.paths).find(
        (path) => path.replace(/\{[^}]*\}/g, '{}') === contract.path,
      )
      const responses = changed.paths[path][contract.method].responses
      if (responses['204']) {
        responses['204'].content = {
          'application/json': { schema: { type: 'object' } },
        }
        expect(() => checkContract(changed, contract)).toThrow(
          'no-content response unexpectedly has a body',
        )
      } else {
        for (const response of Object.values(responses))
          for (const media of Object.values(response.content ?? {}))
            if (media.schema?.type === 'string') media.schema.type = 'object'
        expect(() => checkContract(changed, contract)).toThrow(
          'API text success response has no string schema',
        )
      }
    }
  })
})
