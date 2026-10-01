/**
 * Verifies each listed route is deep-linkable through its query parameter and
 * renders without a preselected entity.
 */
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const routeRoot = resolve(import.meta.dirname, '../../routes')

const queryAddressableRoutes = [
  ['apis.tsx', 'apiId'],
  ['bi.tsx', 'surfaceId'],
  ['branches.tsx', 'branchId'],
  ['extensions.tsx', 'extensionId'],
  ['governance.tsx', 'datasetId'],
  ['lineage.tsx', 'assetId'],
  ['logs.tsx', 'logId'],
  ['observability.tsx', 'providerId'],
  ['operations.tsx', 'operationId'],
  ['pipelines.tsx', 'pipelineId'],
  ['publishing.tsx', 'datasetId'],
  ['quality.tsx', 'checkId'],
  ['runs.tsx', 'runId'],
  ['storage.tsx', 'providerId'],
  ['tables.tsx', 'tableId'],
] as const

function routeSource(fileName: string): string {
  return readFileSync(resolve(routeRoot, fileName), 'utf8')
}

describe('Observatory workflow deep links', () => {
  it.each(queryAddressableRoutes)(
    '%s preserves selected workflow state in ?%s=...',
    (fileName, queryKey) => {
      const source = routeSource(fileName)

      expect(source).toContain(`searchParams.set('${queryKey}'`)
      expect(source).toMatch(
        new RegExp(
          `URLSearchParams\\(window\\.location\\.search\\)\\.get\\(\\s*'${queryKey}'`,
        ),
      )
    },
  )

  it('does not wire table/list row selection directly to local selectedId state', () => {
    const routeSources = queryAddressableRoutes
      .map(([fileName]) => routeSource(fileName))
      .join('\n')

    expect(routeSources).not.toMatch(
      /on(?:Click|Select)=\{(?:\(\) => )?setSelectedId/,
    )
    expect(routeSources).not.toContain('onSelect={setSelectedId}')
  })

  it('reads service health from v1 without exposing legacy mutations', () => {
    const source = routeSource('services.tsx')

    expect(source).toContain('getObservatoryServices')
    expect(source).toContain("'observatory:v1:services'")
    expect(source).toContain('service detail, start,')
    expect(source).not.toContain('runObservatoryAction')
    expect(source).not.toContain('getObservatoryServiceDetail')
  })

  it('keeps Dataset profile links on query-selected workflow pages', () => {
    const source = routeSource('datasets.$datasetId.tsx')

    expect(source).toContain("datasetV1Unavailable('detail')")
    expect(source).toContain('Dagster asset data as Dataset evidence')
    expect(source).not.toContain('getObservatoryDatasetProfile')
    expect(source).not.toContain('getObservatoryAssetRecords')
  })

  it('keeps Tables workflow links on query-selected workflow pages', () => {
    const source = routeSource('tables.tsx')
    const lineageSource = routeSource('lineage.tsx')
    const branchesSource = routeSource('branches.tsx')
    const logsSource = routeSource('logs.tsx')
    const operationsSource = routeSource('operations.tsx')
    const governanceSource = routeSource('governance.tsx')
    const commandPaletteSource = readFileSync(
      resolve(import.meta.dirname, '../shell/ObservatoryCommandPalette.tsx'),
      'utf8',
    )
    const shellSource = readFileSync(
      resolve(import.meta.dirname, '../shell/ObservatoryShell.tsx'),
      'utf8',
    )

    expect(source).toContain('getV1AssetDetail')
    expect(source).toContain('getV1AssetPreview')
    expect(source).toContain('to="/lineage" search={{ assetId: asset.id }}')
    expect(source).toContain('detail.data.dependencies.map')
    expect(lineageSource).toContain(
      '`/tables?tableId=${encodeURIComponent(firstTable.id)}`',
    )
    expect(lineageSource).toContain('search={{ tableId: table.id }}')
    expect(branchesSource).toContain(
      "URLSearchParams(window.location.search).get('tableId')",
    )
    expect(branchesSource).toContain('search={{ tableId }}')
    expect(logsSource).toContain(
      'return `/lineage?assetId=${encodeURIComponent(resource.id)}`',
    )
    expect(logsSource).toContain(
      'return `/tables?tableId=${encodeURIComponent(resource.id)}`',
    )
    expect(operationsSource).toContain(
      'return `/tables?tableId=${encodeURIComponent(resource.id)}`',
    )
    expect(operationsSource).toContain(
      'return `/lineage?assetId=${encodeURIComponent(resource.id)}`',
    )
    expect(governanceSource).toContain(
      'return `/tables?tableId=${encodeURIComponent(resource.id)}`',
    )
    expect(governanceSource).toContain(
      'return `/lineage?assetId=${encodeURIComponent(resource.id)}`',
    )
    expect(commandPaletteSource).toContain(
      'value={`open:/tables?tableId=${encodeURIComponent(table.id)}`}',
    )
    expect(source).not.toContain(
      '`/lineage/${encodeURIComponent(selected.asset_id)}`',
    )
    expect(source).not.toContain('to="/lineage/$assetId"')
    expect(lineageSource).not.toContain('to="/tables/$tableId"')
    expect(lineageSource).not.toContain('to="/lineage/$assetId"')
    expect(branchesSource).not.toContain('to="/tables/$tableId"')
    expect(logsSource).not.toContain("'/lineage/$assetId'")
    expect(logsSource).not.toContain("'/tables/$tableId'")
    expect(operationsSource).not.toContain(
      '`/tables/${encodeURIComponent(resource.id)}`',
    )
    expect(operationsSource).not.toContain(
      '`/lineage/${encodeURIComponent(resource.id)}`',
    )
    expect(governanceSource).not.toContain(
      '`/tables/${encodeURIComponent(resource.id)}`',
    )
    expect(governanceSource).not.toContain(
      '`/lineage/${encodeURIComponent(resource.id)}`',
    )
    expect(commandPaletteSource).not.toContain('value={`open:/tables/${')
    expect(shellSource).not.toContain("'/tables/': 'tables'")
    expect(shellSource).not.toContain("'/lineage/': 'lineage'")
    expect(shellSource).not.toContain("'/branches/': 'branches'")
    expect(existsSync(resolve(routeRoot, 'tables/$tableId.tsx'))).toBe(false)
    expect(existsSync(resolve(routeRoot, 'lineage/$assetId.tsx'))).toBe(false)
    expect(existsSync(resolve(routeRoot, 'branches/$branchName.tsx'))).toBe(
      false,
    )
  })

  it('refreshes selected table previews from the live API instead of trusting persisted cache', () => {
    const source = routeSource('tables.tsx')

    expect(source).toContain('getV1AssetPreview({')
    expect(source).toContain('data: { environment, assetId: selectedId }')
    expect(source).not.toContain('loadCachedResource')
    expect(source).not.toContain('readMetric')
    expect(routeSource('runs.tsx')).toContain("'observatory:runs'")
    expect(routeSource('runs.tsx')).not.toContain("'observatory:operations'")
  })

  it('keeps Quality evidence environment-scoped and URL-backed', () => {
    const source = routeSource('quality.tsx')

    expect(source).toContain('getV1QualitySnapshot')
    expect(source).toContain('selectedEnvironment')
    expect(source).toContain('environmentChangeEvent')
    expect(source).toContain("url.searchParams.set('checkId', row.id)")
    expect(source).toContain('/lineage?assetId=')
    expect(source).toContain('/runs?runId=')
    expect(source).toContain('It is not counted as passing.')
  })

  it('keeps Runs evidence aligned to Dataset terminology', () => {
    const source = routeSource('runs.tsx')

    expect(source).toContain('Affected Datasets')
    expect(source).not.toContain('Dataset links')
    expect(source).not.toContain('Dataset refs')
    expect(source).not.toContain('Dataset lineage')
    expect(source).not.toContain('Lineage resources')
    expect(source).not.toContain('lineage resource')
  })

  it('keeps Operations selection URL-backed even without an incoming operationId', () => {
    const source = routeSource('operations.tsx')

    expect(source).toContain('selectOperation(initial.id)')
    expect(source).toContain('chooseDefaultOperation(visibleOperations)')
    expect(source).toContain(
      "operations.find((operation) => operation.status === 'failed')",
    )
  })

  it('keeps Publishing and Governance defaults URL-backed without overwriting valid incoming links', () => {
    const publishingSource = routeSource('publishing.tsx')
    const governanceSource = routeSource('governance.tsx')

    expect(publishingSource).toContain('selectDataset(selected.id)')
    expect(publishingSource).toContain(
      'requested && promoted.some((dataset) => dataset.id === requested)',
    )
    expect(governanceSource).toContain('selectDataset(selected.dataset.id)')
    expect(governanceSource).toContain(
      'requested && rows.some((row) => row.dataset.id === requested)',
    )
  })

  it('keeps Publishing readiness from treating missing evidence as ready', () => {
    const source = routeSource('publishing.tsx')
    const datasetSource = routeSource('datasets.$datasetId.tsx')

    // Missing evidence is surfaced from the canonical verdict as its own
    // pending state, never folded into a ready claim.
    expect(source).toContain('readiness.missing_evidence')
    expect(source).toContain('Needs evidence')
    expect(source).toContain('missing evidence')
    expect(source).toContain("data-state={readiness?.state ?? 'unknown'}")
    expect(source).not.toContain("'owner missing'")
    expect(datasetSource).toContain('Dataset detail unavailable')
    expect(datasetSource).not.toContain('Release controls clear')
    expect(datasetSource).not.toContain('datasetPublishingIssues(profile)')
  })
})
