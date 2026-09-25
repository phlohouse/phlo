/**
 * Contributing Rows Server Functions
 *
 * Thin wrappers that forward contributing-row provenance requests to phlo-api.
 */

import { createServerFn } from '@tanstack/react-start'

import type { DataRow } from '@/observatory/api/trino'
import { authMiddleware } from '@/observatory/api/auth'
import { describePhloApiError } from '@/observatory/api/errors'
import { apiPost } from '@/server/phlo-api'
import { camelizeKeys } from '@/utils/caseTransform'

type ContributingRowsMode = 'entity' | 'aggregate'

export type ContributingRowsQueryResult =
  | { query: string; upstream: { schema: string; table: string } }
  | { error: string }

export type ContributingRowsPageResult =
  | {
      mode: ContributingRowsMode
      page: number
      pageSize: number
      hasMore: boolean
      query: string
      upstream: { schema: string; table: string }
      columns: Array<string>
      columnTypes: Array<string>
      rows: Array<DataRow>
    }
  | { error: string }

interface ApiContributingRowsQueryResult {
  query: string
  upstream: { schema: string; table: string }
}

interface ApiContributingRowsPageResult {
  mode: ContributingRowsMode
  page: number
  page_size: number
  has_more: boolean
  query: string
  upstream: { schema: string; table: string }
  columns: Array<string>
  column_types: Array<string>
  rows: Array<DataRow>
}

export function transformContributingRowsQueryResult(
  result: ApiContributingRowsQueryResult,
): Exclude<ContributingRowsQueryResult, { error: string }> {
  return result
}

export function transformContributingRowsPageResult(
  result: ApiContributingRowsPageResult,
): Exclude<ContributingRowsPageResult, { error: string }> {
  const transformed = camelizeKeys<
    Omit<Exclude<ContributingRowsPageResult, { error: string }>, 'rows'>
  >({
    mode: result.mode,
    page: result.page,
    page_size: result.page_size,
    has_more: result.has_more,
    query: result.query,
    upstream: result.upstream,
    columns: result.columns,
    column_types: result.column_types,
  })

  return {
    ...transformed,
    rows: result.rows,
  }
}

export async function fetchContributingRowsQueryFromApi(data: {
  downstreamAssetKey: string
  upstreamAssetKey: string
  rowData: Record<string, unknown>
  limit?: number
  trinoUrl?: string
  timeoutMs?: number
  catalog?: string
}): Promise<ApiContributingRowsQueryResult> {
  return apiPost<ApiContributingRowsQueryResult>(
    '/api/observatory/contributing-rows/query',
    {
      downstream_asset_key: data.downstreamAssetKey,
      upstream_asset_key: data.upstreamAssetKey,
      row_data: data.rowData,
      limit: data.limit,
      trino_url: data.trinoUrl,
      timeout_ms: data.timeoutMs,
      catalog: data.catalog,
    },
  )
}

export async function fetchContributingRowsPageFromApi(data: {
  downstreamAssetKey: string
  upstreamAssetKey: string
  rowData: Record<string, unknown>
  page?: number
  pageSize?: number
  trinoUrl?: string
  timeoutMs?: number
  catalog?: string
}): Promise<ApiContributingRowsPageResult> {
  return apiPost<ApiContributingRowsPageResult>(
    '/api/observatory/contributing-rows/page',
    {
      downstream_asset_key: data.downstreamAssetKey,
      upstream_asset_key: data.upstreamAssetKey,
      row_data: data.rowData,
      page: data.page,
      page_size: data.pageSize,
      trino_url: data.trinoUrl,
      timeout_ms: data.timeoutMs,
      catalog: data.catalog,
    },
  )
}

export const getContributingRowsQuery = createServerFn()
  .middleware([authMiddleware])
  .inputValidator(
    (input: {
      downstreamAssetKey: string
      upstreamAssetKey: string
      rowData: Record<string, unknown>
      limit?: number
      trinoUrl?: string
      timeoutMs?: number
      catalog?: string
    }) => input,
  )
  .handler(async ({ data }): Promise<ContributingRowsQueryResult> => {
    try {
      return transformContributingRowsQueryResult(
        await fetchContributingRowsQueryFromApi(data),
      )
    } catch (error) {
      return { error: describePhloApiError(error).message }
    }
  })

export const getContributingRowsPage = createServerFn()
  .middleware([authMiddleware])
  .inputValidator(
    (input: {
      downstreamAssetKey: string
      upstreamAssetKey: string
      rowData: Record<string, unknown>
      page?: number
      pageSize?: number
      trinoUrl?: string
      timeoutMs?: number
      catalog?: string
    }) => input,
  )
  .handler(async ({ data }): Promise<ContributingRowsPageResult> => {
    try {
      return transformContributingRowsPageResult(
        await fetchContributingRowsPageFromApi(data),
      )
    } catch (error) {
      return { error: describePhloApiError(error).message }
    }
  })
