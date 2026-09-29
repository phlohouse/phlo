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
const logsClient = readFileSync(new URL('./logsV1.ts', import.meta.url), 'utf8')

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
})
