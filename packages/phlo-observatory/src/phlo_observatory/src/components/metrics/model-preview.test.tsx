// @vitest-environment jsdom
/** Metric preview submits generated SQL through the existing Query boundary. */
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MetricModelPreview } from './model-preview'
import type { ReactNode } from 'react'
import type { MetricModel } from '@/lib/data/api/metrics'

const previewSql = vi.hoisted(() => vi.fn())
const execute = vi.hoisted(() => vi.fn())
vi.mock('@/lib/data/api/metrics', () => ({ getMetricPreviewSql: previewSql }))
vi.mock('@/lib/data/api/query', () => ({
  submitQuery: execute,
  getQuerySession: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
const model: MetricModel = {
  model_id: 'batch_release',
  description: 'Synthetic release data',
  owner: 'QC',
  asset_id: 'gold/batches',
  dimensions: [
    { name: 'product', column: 'product', kind: 'string', description: '' },
  ],
  measures: [
    {
      name: 'batches',
      column: 'batch_id',
      aggregation: 'count',
      description: '',
    },
  ],
  time_dimension: null,
  env: 'sandbox',
  nessie_ref: 'main',
  relation: 'gold.batches',
  version: 1,
  status: 'draft',
  created_at: '2026-10-22T10:00:00Z',
  updated_at: '2026-10-22T10:00:00Z',
}

describe('metric preview', () => {
  it('executes the exact generated SQL with selected dimensions and displays returned rows', async () => {
    const sql =
      'SELECT "product", COUNT("batch_id") AS "batches" FROM "gold"."batches" GROUP BY1'
    previewSql.mockResolvedValue({
      env: 'sandbox',
      model_id: model.model_id,
      version: 1,
      sql,
      source: 'draft_metric_definition',
    })
    execute.mockResolvedValue({
      id: 'real-query-id',
      env: 'sandbox',
      status: 'completed',
      result: {
        columns: [{ name: 'batches', type: 'bigint' }],
        rows: [{ batches: 24 }],
        has_more: false,
      },
      error: null,
    })
    const user = userEvent.setup()
    render(<MetricModelPreview model={model} env="sandbox" />)
    await user.click(screen.getByRole('checkbox', { name: /product/ }))
    await user.click(screen.getByRole('button', { name: 'Run preview' }))
    expect((await screen.findByRole('status')).textContent).toContain(
      '1 returned rows',
    )
    expect(execute).toHaveBeenCalledWith({ data: { env: 'sandbox', sql } })
    expect(previewSql).toHaveBeenCalledWith({
      data: { env: 'sandbox', id: 'batch_release', group_by: ['product'] },
    })
    expect(screen.getByRole('cell', { name: '24' })).toBeTruthy()
  })
  it('does not execute stale model SQL or bypass a denied Query permission', async () => {
    previewSql.mockResolvedValue({ version: 2, sql: 'SELECT1' })
    const user = userEvent.setup()
    const rendered = render(<MetricModelPreview model={model} env="sandbox" />)
    await user.click(screen.getByRole('button', { name: 'Run preview' }))
    expect((await screen.findByRole('alert')).textContent).toContain(
      'model changed',
    )
    expect(execute).not.toHaveBeenCalled()
    rendered.unmount()
    previewSql.mockResolvedValue({ version: 1, sql: 'SELECT1' })
    execute.mockRejectedValue(new Error('Dataset query permission denied'))
    render(<MetricModelPreview model={model} env="sandbox" />)
    await user.click(screen.getByRole('button', { name: 'Run preview' }))
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Dataset query permission denied',
    )
  })
})
