// @vitest-environment jsdom
/** A model edit keeps source identity, typed fields and the expected draft version. */
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MetricModelEditor } from './model-editor'
import type { MetricModel } from '@/lib/data/api/metrics'

const source = vi.hoisted(() => vi.fn())
const save = vi.hoisted(() => vi.fn())
vi.mock('@/lib/data/api/metrics', () => ({
  getMetricSource: source,
  saveMetricModel: save,
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
  dimensions: [],
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
  version: 3,
  status: 'draft',
  created_at: '',
  updated_at: '',
}

describe('metric model editor', () => {
  it('submits edits with the current version and preserves a rejected draft for recovery', async () => {
    source.mockResolvedValue({
      id: 'gold/batches',
      relation: 'gold.batches',
      columns: [{ name: 'batch_id', type: 'string', description: null }],
    })
    save.mockRejectedValue(
      new Error('The metric model changed. Reload before saving.'),
    )
    const user = userEvent.setup()
    render(
      <MetricModelEditor
        model={model}
        env="sandbox"
        sources={[{ id: 'gold/batches', relation: 'gold.batches' }]}
        onSaved={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    const owner = screen.getByLabelText('Owner')
    await user.clear(owner)
    await user.type(owner, 'QA owner')
    await user.click(
      await screen.findByRole('button', { name: 'Save draft model' }),
    )
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Reload before saving',
    )
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          env: 'sandbox',
          expected_version: 3,
          definition: expect.objectContaining({
            owner: 'QA owner',
            asset_id: 'gold/batches',
          }),
        }),
      }),
    )
    expect((owner as HTMLInputElement).value).toBe('QA owner')
  })
})
