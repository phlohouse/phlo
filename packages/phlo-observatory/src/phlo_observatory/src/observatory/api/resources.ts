/**
 * Observatory resource access layer.
 *
 * One endpoint surface, two transports: during SSR the getters call phlo-api
 * server-side via @/server/phlo-api; in the browser the `*Direct` variants hit
 * phlo-api directly using a base URL injected into the page
 * (window.__PHLO_API_BROWSER_URL__ or the phlo-api-browser-url meta tag).
 *
 * Every getter returns {data, error} and never throws: transport failures are
 * reported as error strings so pages can render a degraded state instead of
 * failing the request.
 */
import { createMiddleware, createServerFn } from '@tanstack/react-start'

import type {
  ObservatoryActionResult,
  ObservatoryAsset,
  ObservatoryAssetDetail,
  ObservatoryBranch,
  ObservatoryBranchDetail,
  ObservatoryCapabilities,
  ObservatoryDataset,
  ObservatoryDatasetFacets,
  ObservatoryDatasetListPage,
  ObservatoryDatasetPage,
  ObservatoryDatasetPipeline,
  ObservatoryDatasetProfile,
  ObservatoryDatasetWorkflowConfig,
  ObservatoryExtension,
  ObservatoryExtensionDetail,
  ObservatoryGovernanceMatrix,
  ObservatoryLogEvent,
  ObservatoryLogFacets,
  ObservatoryMetadata,
  ObservatoryOperation,
  ObservatoryOperationDetail,
  ObservatoryOverview,
  ObservatoryPackageInstallResult,
  ObservatoryPublishingReadinessItem,
  ObservatoryQualityCheck,
  ObservatoryQualityDetail,
  ObservatoryQueryResult,
  ObservatoryResourceItem,
  ObservatoryResourceResult,
  ObservatoryRowJourney,
  ObservatoryRun,
  ObservatoryRunReport,
  ObservatoryRuntimeSettings,
  ObservatorySavedQuery,
  ObservatorySearchListPage,
  ObservatorySearchPage,
  ObservatorySearchResult,
  ObservatoryService,
  ObservatoryServiceDetail,
  ObservatorySurfaceItem,
  ObservatoryTable,
  ObservatoryTablePreview,
  ObservatoryWorkflowActionResult,
  ObservatoryWorkflowProposal,
  ObservatoryWorkflowProposalRequest,
  ObservatoryWorkflowWizardPayload,
} from './types'
import type {
  DatasetFilters,
  SearchFilters,
} from '@/observatory/api/datasetDiscovery'
import { apiGet, apiPost } from '@/server/phlo-api'
import { mutationAuthorization } from '@/server/authenticated-mutation'
import {
  datasetPageQuery,
  searchPageQuery,
} from '@/observatory/api/datasetDiscovery'

const Observatory_API_PREFIX = '/api/observatory'

// Only forward well-formed Bearer headers to phlo-api; anything else is
// dropped (undefined) rather than rejected, so unauthenticated report views
// still render their error states server-side.
function bearerAuthorization(value: string | null): string | undefined {
  if (value === null) return undefined
  return /^Bearer\s+\S+$/i.test(value) ? value : undefined
}

const observatoryReportAuthorization = createMiddleware({
  type: 'request',
}).server(({ next, request }) =>
  next({
    context: {
      authorization: bearerAuthorization(request.headers.get('authorization')),
    },
  }),
)

declare global {
  interface Window {
    __PHLO_API_BROWSER_URL__?: string
  }
}

function apiUnavailable<T>(error: unknown): ObservatoryResourceResult<T> {
  return {
    data: null,
    error:
      error instanceof Error ? error.message : 'Lakehouse API is unavailable',
  }
}

function browserApiBase(): string | null {
  if (typeof window === 'undefined') return null
  const configured =
    window.__PHLO_API_BROWSER_URL__ ??
    document.querySelector<HTMLMetaElement>('meta[name="phlo-api-browser-url"]')
      ?.content
  return configured?.trim() ?? ''
}

async function browserApiGet<T>(endpoint: string): Promise<T> {
  const base = browserApiBase()
  if (base === null) {
    throw new Error('Browser API fallback is unavailable during SSR')
  }
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 8000)
  let response: Response
  try {
    response = await fetch(`${base}${endpoint}`, {
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('phlo-api request timed out')
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
  if (!response.ok) {
    throw new Error(`phlo-api error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

async function browserApiPost<T>(
  endpoint: string,
  body: unknown,
  timeoutMs = 12000,
): Promise<T> {
  if (process.env.NODE_ENV === 'production') {
    throw new Error(
      'Direct browser mutations are disabled in production; route through the server function',
    )
  }
  const base = browserApiBase()
  if (base === null) {
    throw new Error('Browser API fallback is unavailable during SSR')
  }
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(`${base}${endpoint}`, {
      body: JSON.stringify(body),
      headers: { 'content-type': 'application/json' },
      method: 'POST',
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('phlo-api request timed out')
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload
        ? String(payload.detail)
        : `${response.status} ${response.statusText}`
    throw new Error(`phlo-api error: ${detail}`)
  }
  return response.json()
}

async function browserApiPut<T>(
  endpoint: string,
  body: unknown,
  timeoutMs = 12000,
): Promise<T> {
  if (process.env.NODE_ENV === 'production') {
    throw new Error(
      'Direct browser mutations are disabled in production; route through the server function',
    )
  }
  const base = browserApiBase()
  if (base === null) {
    throw new Error('Browser API fallback is unavailable during SSR')
  }
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(`${base}${endpoint}`, {
      body: JSON.stringify(body),
      headers: { 'content-type': 'application/json' },
      method: 'PUT',
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('phlo-api request timed out')
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload
        ? String(payload.detail)
        : `${response.status} ${response.statusText}`
    throw new Error(`phlo-api error: ${detail}`)
  }
  return response.json()
}

async function observatoryApiGet<T>(endpoint: string): Promise<T> {
  if (typeof window !== 'undefined') {
    if (browserApiBase() !== null) return browserApiGet<T>(endpoint)
    throw new Error('Browser API base URL is not configured')
  }
  return apiGet<T>(endpoint, undefined, 8000)
}

export async function getObservatoryOverview(): Promise<
  ObservatoryResourceResult<ObservatoryOverview>
> {
  try {
    const data = await observatoryApiGet<ObservatoryOverview>(
      `${Observatory_API_PREFIX}/overview`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryOverview>(error)
  }
}

export async function getObservatoryCapabilities(): Promise<
  ObservatoryResourceResult<ObservatoryCapabilities>
> {
  try {
    const data = await observatoryApiGet<ObservatoryCapabilities>(
      `${Observatory_API_PREFIX}/surface-capabilities`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryCapabilities>(error)
  }
}

export async function getObservatoryServices(): Promise<
  ObservatoryResourceResult<Array<ObservatoryService>>
> {
  try {
    const response = await observatoryApiGet<{
      items: Array<ObservatoryService>
    }>(`${Observatory_API_PREFIX}/services`)
    return { data: response.items, error: null }
  } catch (error) {
    return apiUnavailable<Array<ObservatoryService>>(error)
  }
}

export async function getObservatoryServicesDirect(): Promise<
  ObservatoryResourceResult<Array<ObservatoryService>>
> {
  try {
    const response = await browserApiGet<{ items: Array<ObservatoryService> }>(
      `${Observatory_API_PREFIX}/services`,
    )
    return { data: response.items, error: null }
  } catch (error) {
    return apiUnavailable<Array<ObservatoryService>>(error)
  }
}

export const getObservatoryServiceDetail = createServerFn()
  .inputValidator((input: { serviceId: string }) => input)
  .handler(
    async ({
      data: { serviceId },
    }): Promise<ObservatoryResourceResult<ObservatoryServiceDetail>> => {
      try {
        const data = await apiGet<ObservatoryServiceDetail>(
          `${Observatory_API_PREFIX}/services/${encodeURIComponent(serviceId)}`,
          undefined,
          8000,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryServiceDetail>(error)
      }
    },
  )

export async function getObservatoryServiceDetailDirect({
  serviceId,
}: {
  serviceId: string
}): Promise<ObservatoryResourceResult<ObservatoryServiceDetail>> {
  try {
    const data = await browserApiGet<ObservatoryServiceDetail>(
      `${Observatory_API_PREFIX}/services/${encodeURIComponent(serviceId)}`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryServiceDetail>(error)
  }
}

async function getRawCollection<T>(
  endpoint: string,
): Promise<ObservatoryResourceResult<Array<T>>> {
  try {
    const response = await observatoryApiGet<{ items: Array<T> }>(
      `${Observatory_API_PREFIX}/${endpoint}`,
    )
    return { data: response.items, error: null }
  } catch (error) {
    return apiUnavailable<Array<T>>(error)
  }
}

async function getRawResource<T>(
  endpoint: string,
): Promise<ObservatoryResourceResult<T>> {
  try {
    const response = await observatoryApiGet<T>(
      `${Observatory_API_PREFIX}/${endpoint}`,
    )
    return { data: response, error: null }
  } catch (error) {
    return apiUnavailable<T>(error)
  }
}

async function getCollection(
  endpoint: string,
): Promise<ObservatoryResourceResult<Array<ObservatoryResourceItem>>> {
  try {
    const response = await observatoryApiGet<{
      items: Array<Record<string, unknown>>
    }>(`${Observatory_API_PREFIX}/${endpoint}`)
    return {
      data: response.items.map((item) => normalizeItem(endpoint, item)),
      error: null,
    }
  } catch (error) {
    return apiUnavailable<Array<ObservatoryResourceItem>>(error)
  }
}

export function getObservatoryOperationRecords() {
  return getRawCollection<ObservatoryOperation>('operations')
}

export async function getObservatoryOperationRecordsDirect(): Promise<
  ObservatoryResourceResult<Array<ObservatoryOperation>>
> {
  try {
    const response = await browserApiGet<{
      items: Array<ObservatoryOperation>
    }>(`${Observatory_API_PREFIX}/operations`)
    return { data: response.items, error: null }
  } catch (error) {
    return apiUnavailable<Array<ObservatoryOperation>>(error)
  }
}

export const getObservatoryOperationDetail = createServerFn()
  .inputValidator((input: { operationId: string }) => input)
  .handler(
    async ({
      data: { operationId },
    }): Promise<ObservatoryResourceResult<ObservatoryOperationDetail>> => {
      try {
        const data = await apiGet<ObservatoryOperationDetail>(
          `${Observatory_API_PREFIX}/operations/${encodeURIComponent(operationId)}`,
          undefined,
          8000,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryOperationDetail>(error)
      }
    },
  )

export async function getObservatoryOperationDetailDirect({
  operationId,
}: {
  operationId: string
}): Promise<ObservatoryResourceResult<ObservatoryOperationDetail>> {
  try {
    const data = await browserApiGet<ObservatoryOperationDetail>(
      `${Observatory_API_PREFIX}/operations/${encodeURIComponent(operationId)}`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryOperationDetail>(error)
  }
}

export function getObservatoryRunRecords() {
  return getRawCollection<ObservatoryRun>('runs')
}

export type ObservatoryRunReportErrorCode =
  | 'access_denied'
  | 'not_found'
  | 'request_failed'
  | 'invalid_request'

export type ObservatoryRunReportResult =
  ObservatoryResourceResult<ObservatoryRunReport> & {
    errorCode?: ObservatoryRunReportErrorCode
  }

type ObservatoryRunReportInput = {
  attempt: number
  projectId: string
  runId: string
}

function parseObservatoryRunReportInput(
  input: unknown,
): ObservatoryRunReportInput | null {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return null

  const { attempt, projectId, runId } = input as Record<string, unknown>
  if (
    typeof projectId !== 'string' ||
    !projectId.trim() ||
    typeof runId !== 'string' ||
    !runId.trim()
  ) {
    return null
  }

  const parsedAttempt =
    typeof attempt === 'number'
      ? attempt
      : typeof attempt === 'string' && /^[1-9]\d*$/.test(attempt)
        ? Number(attempt)
        : Number.NaN
  if (!Number.isSafeInteger(parsedAttempt) || parsedAttempt < 1) return null

  return { attempt: parsedAttempt, projectId, runId }
}

export const getObservatoryRunReport = createServerFn()
  .middleware([observatoryReportAuthorization])
  .inputValidator(parseObservatoryRunReportInput)
  .handler(async ({ data, context }): Promise<ObservatoryRunReportResult> => {
    if (!data) {
      return {
        data: null,
        error:
          'Enter a project, run, and positive attempt number to open a report.',
        errorCode: 'invalid_request',
      }
    }

    const { attempt, projectId, runId } = data

    try {
      const data = await apiGet<ObservatoryRunReport>(
        `${Observatory_API_PREFIX}/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/attempts/${attempt}/report`,
        undefined,
        8000,
        context.authorization,
      )
      return { data, error: null }
    } catch (error) {
      // phlo-api failures arrive as plain Errors whose message embeds the HTTP
      // status ("phlo-api error: 404 ...", produced by @/server/phlo-api), so
      // classification has to recover the status from that text.
      const status = Number(
        error instanceof Error
          ? error.message.match(/^phlo-api error: (401|403|404)\b/)?.[1]
          : undefined,
      )
      if (status === 401 || status === 403) {
        return {
          data: null,
          error:
            'Access denied: this account cannot read the requested run report.',
          errorCode: 'access_denied',
        }
      }
      if (status === 404) {
        return {
          data: null,
          error:
            'No report was found for the requested project, run, and attempt.',
          errorCode: 'not_found',
        }
      }
      return {
        data: null,
        error: 'The run report request failed. Please try again.',
        errorCode: 'request_failed',
      }
    }
  })

export function getObservatoryStorageItems() {
  return getRawCollection<ObservatorySurfaceItem>('storage')
}

export function getObservatoryObservabilityItems() {
  return getRawCollection<ObservatorySurfaceItem>('observability')
}

export function getObservatoryGovernanceItems() {
  return getRawResource<ObservatoryGovernanceMatrix>('governance')
}

export function getObservatoryApiItems() {
  return getRawCollection<ObservatorySurfaceItem>('apis')
}

export function getObservatoryBiItems() {
  return getRawCollection<ObservatorySurfaceItem>('bi')
}

export function getObservatoryAssetRecords() {
  return getRawCollection<ObservatoryAsset>('assets')
}

export function getObservatoryDatasetRecords() {
  return getRawCollection<ObservatoryDataset>('datasets')
}

export async function getObservatoryDatasetPage({
  cursor = null,
  filters,
  limit = 100,
}: {
  cursor?: string | null
  filters: DatasetFilters
  limit?: number
}): Promise<ObservatoryResourceResult<ObservatoryDatasetPage>> {
  try {
    // Unlike getRawCollection, the list envelope (including next_cursor) is
    // preserved so the route can page through the full filtered collection.
    const response = await observatoryApiGet<ObservatoryDatasetListPage>(
      `${Observatory_API_PREFIX}/datasets${datasetPageQuery(filters, limit, cursor)}`,
    )
    return {
      data: { items: response.items, nextCursor: response.next_cursor ?? null },
      error: null,
    }
  } catch (error) {
    return apiUnavailable<ObservatoryDatasetPage>(error)
  }
}

export async function getObservatoryDatasetFacets(): Promise<
  ObservatoryResourceResult<ObservatoryDatasetFacets>
> {
  try {
    const data = await observatoryApiGet<ObservatoryDatasetFacets>(
      `${Observatory_API_PREFIX}/datasets/facets`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryDatasetFacets>(error)
  }
}

export async function getObservatoryPublishingReadinessDirect(): Promise<
  ObservatoryResourceResult<Array<ObservatoryPublishingReadinessItem>>
> {
  try {
    const data = await browserApiGet<{
      items: Array<ObservatoryPublishingReadinessItem>
    }>(`${Observatory_API_PREFIX}/datasets/publishing-readiness`)
    return { data: data.items, error: null }
  } catch (error) {
    return apiUnavailable<Array<ObservatoryPublishingReadinessItem>>(error)
  }
}

export async function getObservatoryDatasetWorkflowConfigDirect(): Promise<
  ObservatoryResourceResult<ObservatoryDatasetWorkflowConfig>
> {
  try {
    const data = await browserApiGet<ObservatoryDatasetWorkflowConfig>(
      `${Observatory_API_PREFIX}/dataset-workflow/config`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryDatasetWorkflowConfig>(error)
  }
}

export async function putObservatoryDatasetWorkflowConfigDirect(
  config: ObservatoryDatasetWorkflowConfig,
): Promise<ObservatoryResourceResult<ObservatoryDatasetWorkflowConfig>> {
  try {
    const data = await browserApiPut<ObservatoryDatasetWorkflowConfig>(
      `${Observatory_API_PREFIX}/dataset-workflow/config`,
      config,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryDatasetWorkflowConfig>(error)
  }
}

export function getObservatoryPipelineRecords() {
  return getRawCollection<ObservatoryDatasetPipeline>('pipelines')
}

export const getObservatoryDatasetProfile = createServerFn()
  .inputValidator((input: { datasetId: string }) => input)
  .handler(
    async ({
      data: { datasetId },
    }): Promise<ObservatoryResourceResult<ObservatoryDatasetProfile>> => {
      try {
        const data = await apiGet<ObservatoryDatasetProfile>(
          `${Observatory_API_PREFIX}/datasets/${encodeURIComponent(datasetId)}`,
          undefined,
          8000,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryDatasetProfile>(error)
      }
    },
  )

export async function getObservatoryDatasetProfileDirect({
  datasetId,
}: {
  datasetId: string
}): Promise<ObservatoryResourceResult<ObservatoryDatasetProfile>> {
  try {
    const data = await browserApiGet<ObservatoryDatasetProfile>(
      `${Observatory_API_PREFIX}/datasets/${encodeURIComponent(datasetId)}`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryDatasetProfile>(error)
  }
}

export const getObservatoryAssetDetail = createServerFn()
  .inputValidator((input: { assetId: string }) => input)
  .handler(
    async ({
      data: { assetId },
    }): Promise<ObservatoryResourceResult<ObservatoryAssetDetail>> => {
      try {
        const data = await apiGet<ObservatoryAssetDetail>(
          `${Observatory_API_PREFIX}/assets/${encodeURIComponent(assetId)}`,
          undefined,
          8000,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryAssetDetail>(error)
      }
    },
  )

export async function getObservatoryAssetDetailDirect({
  assetId,
}: {
  assetId: string
}): Promise<ObservatoryResourceResult<ObservatoryAssetDetail>> {
  try {
    const data = await browserApiGet<ObservatoryAssetDetail>(
      `${Observatory_API_PREFIX}/assets/${encodeURIComponent(assetId)}`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryAssetDetail>(error)
  }
}

export function getObservatoryTableRecords() {
  return getRawCollection<ObservatoryTable>('tables')
}

export async function getObservatoryTablePreview({
  data: { tableId, limit = 50, offset = 0 },
}: {
  data: { tableId: string; limit?: number; offset?: number }
}): Promise<ObservatoryResourceResult<ObservatoryTablePreview>> {
  try {
    const endpoint = `${Observatory_API_PREFIX}/table-preview/${encodeURIComponent(tableId)}`
    if (browserApiBase() !== null) {
      const searchParams = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      })
      const data = await browserApiGet<ObservatoryTablePreview>(
        `${endpoint}?${searchParams}`,
      )
      return { data, error: null }
    }
    if (typeof window !== 'undefined') {
      throw new Error('Browser API base URL is not configured')
    }
    const data = await apiGet<ObservatoryTablePreview>(
      endpoint,
      { limit, offset },
      8000,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryTablePreview>(error)
  }
}

export async function runObservatoryQuery({
  data: { sql, branch, limit = 100, offset = 0 },
}: {
  data: { sql: string; branch?: string; limit?: number; offset?: number }
}): Promise<ObservatoryResourceResult<ObservatoryQueryResult>> {
  try {
    const data =
      browserApiBase() !== null
        ? await browserApiPost<ObservatoryQueryResult>(
            `${Observatory_API_PREFIX}/query`,
            {
              sql,
              branch,
              limit,
              offset,
            },
          )
        : await apiPost<ObservatoryQueryResult>(
            `${Observatory_API_PREFIX}/query`,
            { sql, branch, limit, offset },
            12000,
          )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryQueryResult>(error)
  }
}

export async function getObservatorySavedQueries(): Promise<
  ObservatoryResourceResult<Array<ObservatorySavedQuery>>
> {
  try {
    const response = await observatoryApiGet<{
      items: Array<ObservatorySavedQuery>
    }>(`${Observatory_API_PREFIX}/saved-queries`)
    return { data: response.items, error: null }
  } catch (error) {
    return apiUnavailable<Array<ObservatorySavedQuery>>(error)
  }
}

export async function saveObservatoryQuery({
  data: { name, sql, branch, metadata = {} },
}: {
  data: {
    name: string
    sql: string
    branch?: string
    metadata?: Record<string, unknown>
  }
}): Promise<ObservatoryResourceResult<ObservatorySavedQuery>> {
  try {
    const data =
      browserApiBase() !== null
        ? await browserApiPost<ObservatorySavedQuery>(
            `${Observatory_API_PREFIX}/saved-queries`,
            {
              name,
              sql,
              branch,
              metadata,
            },
          )
        : await apiPost<ObservatorySavedQuery>(
            `${Observatory_API_PREFIX}/saved-queries`,
            { name, sql, branch, metadata },
            8000,
          )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatorySavedQuery>(error)
  }
}

export const getObservatoryRowJourney = createServerFn()
  .inputValidator((input: { tableId: string; rowId: string }) => input)
  .handler(
    async ({
      data: { tableId, rowId },
    }): Promise<ObservatoryResourceResult<ObservatoryRowJourney>> => {
      try {
        const data = await apiGet<ObservatoryRowJourney>(
          `${Observatory_API_PREFIX}/row-journey/${encodeURIComponent(tableId)}/${encodeURIComponent(rowId)}`,
          undefined,
          8000,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryRowJourney>(error)
      }
    },
  )

export function getObservatoryQualityRecords() {
  return getRawCollection<ObservatoryQualityCheck>('quality')
}

export const getObservatoryQualityDetail = createServerFn()
  .inputValidator((input: { checkId: string }) => input)
  .handler(
    async ({
      data: { checkId },
    }): Promise<ObservatoryResourceResult<ObservatoryQualityDetail>> => {
      try {
        const data = await apiGet<ObservatoryQualityDetail>(
          `${Observatory_API_PREFIX}/quality/${encodeURIComponent(checkId)}`,
          undefined,
          8000,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryQualityDetail>(error)
      }
    },
  )

export async function getObservatoryQualityDetailDirect({
  checkId,
}: {
  checkId: string
}): Promise<ObservatoryResourceResult<ObservatoryQualityDetail>> {
  try {
    const data = await browserApiGet<ObservatoryQualityDetail>(
      `${Observatory_API_PREFIX}/quality/${encodeURIComponent(checkId)}`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryQualityDetail>(error)
  }
}

export function getObservatoryLogRecords() {
  return getRawCollection<ObservatoryLogEvent>('logs')
}

export function getObservatoryRuntimeSettings() {
  return getRawResource<ObservatoryRuntimeSettings>('settings')
}

export async function getObservatoryLogFacets(): Promise<
  ObservatoryResourceResult<ObservatoryLogFacets>
> {
  try {
    const data = await observatoryApiGet<ObservatoryLogFacets>(
      `${Observatory_API_PREFIX}/logs/facets`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryLogFacets>(error)
  }
}

export function getObservatoryBranches() {
  return getCollection('branches')
}

export function getObservatoryBranchRecords() {
  return getRawCollection<ObservatoryBranch>('branches')
}

export const getObservatoryBranchDetail = createServerFn()
  .inputValidator((input: { branchName: string }) => input)
  .handler(
    async ({
      data: { branchName },
    }): Promise<ObservatoryResourceResult<ObservatoryBranchDetail>> => {
      try {
        const data = await apiGet<ObservatoryBranchDetail>(
          `${Observatory_API_PREFIX}/branches/${encodeURIComponent(branchName)}`,
          undefined,
          8000,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryBranchDetail>(error)
      }
    },
  )

export async function getObservatoryBranchDetailDirect({
  branchName,
}: {
  branchName: string
}): Promise<ObservatoryResourceResult<ObservatoryBranchDetail>> {
  try {
    const data = await browserApiGet<ObservatoryBranchDetail>(
      `${Observatory_API_PREFIX}/branches/${encodeURIComponent(branchName)}`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryBranchDetail>(error)
  }
}

export function getObservatoryExtensions() {
  return getRawCollection<ObservatoryExtension>('extensions')
}

export async function getObservatoryExtensionDetailDirect({
  extensionId,
}: {
  extensionId: string
}): Promise<ObservatoryResourceResult<ObservatoryExtensionDetail>> {
  try {
    const data = await browserApiGet<ObservatoryExtensionDetail>(
      `${Observatory_API_PREFIX}/extensions/${encodeURIComponent(extensionId)}`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryExtensionDetail>(error)
  }
}

export const getObservatoryExtensionDetail = createServerFn()
  .inputValidator((input: { extensionId: string }) => input)
  .handler(
    async ({
      data: { extensionId },
    }): Promise<ObservatoryResourceResult<ObservatoryExtensionDetail>> => {
      try {
        const data = await apiGet<ObservatoryExtensionDetail>(
          `${Observatory_API_PREFIX}/extensions/${encodeURIComponent(extensionId)}`,
          undefined,
          8000,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryExtensionDetail>(error)
      }
    },
  )

export const searchObservatory = createServerFn()
  .inputValidator((input: { query: string }) => input)
  .handler(
    async ({
      data: { query },
    }): Promise<ObservatoryResourceResult<Array<ObservatorySearchResult>>> => {
      try {
        const response = await apiGet<{
          items: Array<ObservatorySearchResult>
        }>(`${Observatory_API_PREFIX}/search`, { q: query }, 8000)
        return { data: response.items, error: null }
      } catch (error) {
        return apiUnavailable<Array<ObservatorySearchResult>>(error)
      }
    },
  )

export async function searchObservatoryDirect({
  query,
}: {
  query: string
}): Promise<ObservatoryResourceResult<Array<ObservatorySearchResult>>> {
  try {
    const response = await browserApiGet<{
      items: Array<ObservatorySearchResult>
    }>(`${Observatory_API_PREFIX}/search?q=${encodeURIComponent(query)}`)
    return { data: response.items, error: null }
  } catch (error) {
    return apiUnavailable<Array<ObservatorySearchResult>>(error)
  }
}

export async function searchObservatoryPage({
  cursor = null,
  filters,
  limit = 100,
}: {
  cursor?: string | null
  filters: SearchFilters
  limit?: number
}): Promise<ObservatoryResourceResult<ObservatorySearchPage>> {
  try {
    // Unlike searchObservatory, the list envelope (including next_cursor) is
    // preserved and kind/owner filters apply server-side before pagination.
    const response = await observatoryApiGet<ObservatorySearchListPage>(
      `${Observatory_API_PREFIX}/search${searchPageQuery(filters, limit, cursor)}`,
    )
    return {
      data: { items: response.items, nextCursor: response.next_cursor ?? null },
      error: null,
    }
  } catch (error) {
    return apiUnavailable<ObservatorySearchPage>(error)
  }
}

export const runObservatoryAction = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator(
    (input: { actionId: string; expectedState?: string | null }) => input,
  )
  .handler(
    async ({
      data: { actionId, expectedState },
      context,
    }): Promise<ObservatoryResourceResult<ObservatoryActionResult>> => {
      try {
        const data = await apiPost<ObservatoryActionResult>(
          `${Observatory_API_PREFIX}/actions`,
          // Dataset transitions carry the exact observed state as the
          // compare-and-set version; other actions ignore the field.
          {
            action_id: actionId,
            ...(expectedState ? { expected_state: expectedState } : {}),
          },
          130000,
          context.authorization,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryActionResult>(error)
      }
    },
  )

export async function runObservatoryActionDirect({
  actionId,
  expectedState,
}: {
  actionId: string
  expectedState?: string | null
}): Promise<ObservatoryResourceResult<ObservatoryActionResult>> {
  try {
    const data = await browserApiPost<ObservatoryActionResult>(
      `${Observatory_API_PREFIX}/actions`,
      // Dataset transitions carry the exact observed state as the
      // compare-and-set version; other actions ignore the field.
      {
        action_id: actionId,
        ...(expectedState ? { expected_state: expectedState } : {}),
      },
      130000,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryActionResult>(error)
  }
}

export const installObservatoryPackage = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator((input: { packageName: string }) => input)
  .handler(
    async ({
      data: { packageName },
      context,
    }): Promise<ObservatoryResourceResult<ObservatoryPackageInstallResult>> => {
      try {
        const data = await apiPost<ObservatoryPackageInstallResult>(
          `${Observatory_API_PREFIX}/packages/install`,
          { package_name: packageName },
          310000,
          context.authorization,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryPackageInstallResult>(error)
      }
    },
  )

export async function installObservatoryPackageDirect({
  packageName,
}: {
  packageName: string
}): Promise<ObservatoryResourceResult<ObservatoryPackageInstallResult>> {
  try {
    const data = await browserApiPost<ObservatoryPackageInstallResult>(
      `${Observatory_API_PREFIX}/packages/install`,
      { package_name: packageName },
      310000,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryPackageInstallResult>(error)
  }
}

export const runObservatoryBranchAction = createServerFn()
  .middleware([mutationAuthorization])
  .inputValidator((input: { actionId: string }) => input)
  .handler(
    async ({
      data: { actionId },
      context,
    }): Promise<ObservatoryResourceResult<ObservatoryActionResult>> => {
      try {
        const data = await apiPost<ObservatoryActionResult>(
          `${Observatory_API_PREFIX}/branches/actions`,
          { action_id: actionId },
          12000,
          context.authorization,
        )
        return { data, error: null }
      } catch (error) {
        return apiUnavailable<ObservatoryActionResult>(error)
      }
    },
  )

export async function getObservatoryWorkflowWizard(): Promise<
  ObservatoryResourceResult<ObservatoryWorkflowWizardPayload>
> {
  try {
    const data = await observatoryApiGet<ObservatoryWorkflowWizardPayload>(
      `${Observatory_API_PREFIX}/workflow-wizard`,
    )
    return { data, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryWorkflowWizardPayload>(error)
  }
}

export async function createObservatoryWorkflowProposal({
  data,
}: {
  data: ObservatoryWorkflowProposalRequest
}): Promise<ObservatoryResourceResult<ObservatoryWorkflowProposal>> {
  try {
    const proposal =
      browserApiBase() !== null
        ? await browserApiPost<ObservatoryWorkflowProposal>(
            `${Observatory_API_PREFIX}/workflow-wizard/proposals`,
            data,
            12000,
          )
        : await apiPost<ObservatoryWorkflowProposal>(
            `${Observatory_API_PREFIX}/workflow-wizard/proposals`,
            data,
            12000,
          )
    return { data: proposal, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryWorkflowProposal>(error)
  }
}

export async function runObservatoryWorkflowAction({
  data: { actionId, proposal },
}: {
  data: { actionId: string; proposal: ObservatoryWorkflowProposal }
}): Promise<ObservatoryResourceResult<ObservatoryWorkflowActionResult>> {
  try {
    const body = { action_id: actionId, proposal_id: proposal.proposal_id }
    const result =
      browserApiBase() !== null
        ? await browserApiPost<ObservatoryWorkflowActionResult>(
            `${Observatory_API_PREFIX}/workflow-wizard/actions`,
            body,
            12000,
          )
        : await apiPost<ObservatoryWorkflowActionResult>(
            `${Observatory_API_PREFIX}/workflow-wizard/actions`,
            body,
            12000,
          )
    return { data: result, error: null }
  } catch (error) {
    return apiUnavailable<ObservatoryWorkflowActionResult>(error)
  }
}

function normalizeItem(
  endpoint: string,
  item: ObservatoryResourceItem | Record<string, unknown>,
): ObservatoryResourceItem {
  if ('kind' in item && typeof item.kind === 'string') {
    return item as ObservatoryResourceItem
  }
  const record = item as Record<string, unknown>

  const id = readString(record, 'id') || readString(record, 'name') || endpoint
  const name = readString(record, 'name') || id
  const health = readHealth(record)
  const status = readString(record, 'status') || readBooleanStatus(record)
  const kind = endpointKind(endpoint, record)

  return {
    id,
    name,
    kind,
    health,
    status,
    summary: summaryFor(endpoint, record),
    updated_at:
      readString(record, 'updated_at') || readString(record, 'timestamp'),
    links: [],
    metadata: readRecord(record, 'metadata'),
  }
}

function summaryFor(endpoint: string, item: Record<string, unknown>): string {
  if (endpoint === 'assets') {
    const group = readString(item, 'group')
    const kinds = readStringList(item, 'kinds')
    return [group, kinds.join(', ')].filter(Boolean).join(' · ') || 'Asset'
  }
  if (endpoint === 'tables') {
    return (
      [readString(item, 'namespace'), readString(item, 'format')]
        .filter(Boolean)
        .join(' · ') || 'Table'
    )
  }
  if (endpoint === 'quality') {
    return (
      [readString(item, 'asset_id'), readString(item, 'severity')]
        .filter(Boolean)
        .join(' · ') || 'Quality check'
    )
  }
  if (endpoint === 'logs') {
    return (
      [readString(item, 'level'), readString(item, 'source')]
        .filter(Boolean)
        .join(' · ') || 'Log event'
    )
  }
  if (endpoint === 'branches') {
    return readBoolean(item, 'current') ? 'Current branch' : 'Branch'
  }
  if (endpoint === 'extensions') {
    return (
      [readString(item, 'version'), readString(item, 'settings_scope')]
        .filter(Boolean)
        .join(' · ') || 'Extension'
    )
  }
  if (endpoint === 'operations') {
    return (
      [readString(item, 'kind'), readString(item, 'completed_at')]
        .filter(Boolean)
        .join(' · ') || 'Operation'
    )
  }
  return readString(item, 'summary') || endpoint
}

function endpointKind(endpoint: string, item: Record<string, unknown>): string {
  if (endpoint === 'quality') return 'quality'
  if (endpoint === 'logs') return 'log'
  if (endpoint === 'tables') return 'table'
  if (endpoint === 'branches') return 'branch'
  if (endpoint === 'extensions') return 'extension'
  return readString(item, 'kind') || endpoint.replace(/s$/, '')
}

function readHealth(
  item: Record<string, unknown>,
): ObservatoryResourceItem['health'] {
  const health = item.health
  if (!isRecord(health)) return null
  const state = readString(health, 'state')
  if (
    state !== 'ok' &&
    state !== 'warning' &&
    state !== 'error' &&
    state !== 'unknown'
  ) {
    return null
  }
  return { state, message: readString(health, 'message') || null }
}

function readBooleanStatus(item: Record<string, unknown>): string | null {
  if (readBoolean(item, 'current')) return 'current'
  if (readBoolean(item, 'enabled')) return 'enabled'
  if (readBoolean(item, 'protected')) return 'protected'
  return null
}

function readString(item: Record<string, unknown>, key: string): string {
  const value = item[key]
  return typeof value === 'string' ? value : ''
}

function readBoolean(item: Record<string, unknown>, key: string): boolean {
  return item[key] === true
}

function readStringList(
  item: Record<string, unknown>,
  key: string,
): Array<string> {
  const value = item[key]
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === 'string')
    : []
}

function readRecord(
  item: Record<string, unknown>,
  key: string,
): ObservatoryMetadata {
  const value = item[key]
  return isRecord(value) ? (value as ObservatoryMetadata) : {}
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
