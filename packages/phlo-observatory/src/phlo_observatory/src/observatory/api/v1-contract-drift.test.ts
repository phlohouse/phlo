// Check bundled client endpoints against the API routes during the v1 cutover.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const apiRoot = resolve(process.cwd(), '../../../phlo-api/src/phlo_api/api')
const resources = readFileSync(
  new URL('./resources.ts', import.meta.url),
  'utf8',
)
const runsRoute = readFileSync(
  new URL('../../routes/runs.tsx', import.meta.url),
  'utf8',
)
const logsRoute = readFileSync(
  new URL('../../routes/logs.tsx', import.meta.url),
  'utf8',
)
const lineageRoute = readFileSync(
  new URL('../../routes/lineage.tsx', import.meta.url),
  'utf8',
)
const overviewRoute = readFileSync(
  new URL('../routes/OverviewRoute.tsx', import.meta.url),
  'utf8',
)
const commandPalette = readFileSync(
  new URL('../shell/ObservatoryCommandPalette.tsx', import.meta.url),
  'utf8',
)
const shell = readFileSync(
  new URL('../shell/ObservatoryShell.tsx', import.meta.url),
  'utf8',
)
const logsClient = readFileSync(new URL('./logsV1.ts', import.meta.url), 'utf8')
const qualityClient = readFileSync(
  new URL('./qualityV1.ts', import.meta.url),
  'utf8',
)

function routerSource(name: string): string {
  return readFileSync(resolve(apiRoot, name), 'utf8')
}

describe('phase 9 v1 client contracts', () => {
  it.each([
    ['overview', 'v1_assets.py'],
    ['services', 'v1.py'],
    ['runs', 'v1_jobs.py'],
  ])('uses the mounted v1 %s route declared by %s', (path, router) => {
    expect(resources).toContain('V1_API_PREFIX')
    expect(resources).toContain(`V1_API_PREFIX}/${path}`)
    expect(routerSource(router)).toContain(`@router.get("/${path}"`)
  })

  it('does not relabel operation records as canonical runs', () => {
    expect(runsRoute).toContain('getObservatoryRunRecords')
    expect(runsRoute).not.toContain('getObservatoryOperationRecords')
    expect(runsRoute).not.toContain('operationsAsRecoveredRuns')
  })

  it('reads log evidence from the mounted v1 run-logs route, not legacy global logs', () => {
    expect(logsRoute).toContain('getSelectedV1RunLogRecords')
    expect(logsRoute).not.toContain('getObservatoryLogRecords')
    expect(resources).not.toContain('getObservatoryLogRecords')
    expect(resources).not.toContain('getObservatoryLogFacets')
    expect(logsClient).toContain(
      '/api/v1/runs/${encodeURIComponent(run_id)}/logs',
    )
    expect(routerSource('v1_jobs.py')).toContain(
      '@router.get("/runs/{run_id}/logs"',
    )
  })

  it('uses v1 run logs for lineage and correlates them only by latest run id', () => {
    expect(lineageRoute).toContain('getSelectedV1RunLogRecords')
    expect(lineageRoute).not.toContain('getObservatoryLogRecords')
    expect(lineageRoute).toContain('latestRunLogsForAsset(selected, logs)')
    expect(lineageRoute).toContain('log.metadata.run_id === runId')
  })

  it('uses the ref-aware v1 query catalog for lineage table inventory', () => {
    expect(lineageRoute).toContain('getObservatoryQueryCatalogTables')
    expect(lineageRoute).not.toContain('getObservatoryTableRecords')
    expect(resources).toContain('getQueryCatalog({ data: { environment } })')
    expect(routerSource('v1_query.py')).toContain(
      '@router.get("/query/catalog"',
    )
  })

  it('uses v1 asset-check definitions and executions for lineage quality', () => {
    expect(lineageRoute).toContain('getV1QualitySnapshot')
    expect(lineageRoute).not.toContain('getObservatoryQualityRecords')
    expect(qualityClient).toContain('/checks')
    expect(routerSource('v1_assets.py')).toContain('/checks')
  })

  it('uses the selected asset v1 preview instead of the legacy table preview', () => {
    expect(lineageRoute).toContain('getV1AssetPreview')
    expect(lineageRoute).not.toContain('getObservatoryTablePreview')
    expect(routerSource('v1_assets.py')).toContain(
      '"/assets/{asset_id:path}/preview"',
    )
  })

  it('uses v1 run logs in the overview instead of legacy global logs', () => {
    expect(overviewRoute).toContain('getSelectedV1RunLogRecords')
    expect(overviewRoute).not.toContain('getObservatoryLogRecords')
    expect(overviewRoute).toContain("'observatory:run-logs'")
  })

  it('uses v1 run logs and query-catalog tables in shell warmers and search', () => {
    expect(shell).toContain('getSelectedV1RunLogRecords')
    expect(shell).not.toContain('getObservatoryLogRecords')
    expect(commandPalette).toContain('getObservatoryQueryCatalogTables')
    expect(commandPalette).not.toContain('getObservatoryTableRecords')
  })
})
