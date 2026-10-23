// @vitest-environment jsdom
/** A draft product requires real inventory selections and review before any write. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NewProduct } from './new-product'
import type * as ProductsModule from '@/lib/data/api/data-products'
import type { ApiAsset } from '@/lib/data/api/assets'
import {
  createDataProduct,
  updateDataProduct,
} from '@/lib/data/api/data-products'

vi.mock('@/lib/data/api/data-products', async (original) => ({
  ...(await original<typeof ProductsModule>()),
  createDataProduct: vi.fn(),
  updateDataProduct: vi.fn(),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
const assets: Array<ApiAsset> = [
  {
    id: 'gold/batch_release_metrics',
    key: ['gold', 'batch_release_metrics'],
    description: null,
    compute_kind: 'python',
    group_name: 'gold',
    is_source: false,
    dependencies: [],
    last_materialization_at: null,
    last_run_id: null,
    relation: 'gold.batch_release_metrics',
    history_scoped: true,
  },
]
describe('new product review', () => {
  it('edits the existing version and preserves selections when returning from review', async () => {
    const product: ProductsModule.DataProduct = {
      product_id: 'batch-record',
      env: 'sandbox',
      name: 'Batch record',
      purpose: 'Original purpose',
      owner: 'Old owner',
      classifications: ['internal'],
      member_dataset_ids: ['gold.batch_release_metrics'],
      version: 4,
      status: 'draft',
      created_at: '',
      updated_at: '',
      members: [],
      sharing_status: 'unavailable',
      sharing_reason: 'Draft only',
    }
    vi.mocked(updateDataProduct).mockResolvedValue({
      ...product,
      version: 5,
      owner: 'New owner',
    })
    const saved = vi.fn()
    render(
      <NewProduct
        env="sandbox"
        product={product}
        assets={assets}
        onCancel={() => {}}
        onCreated={saved}
      />,
    )
    expect(screen.getByLabelText<HTMLInputElement>('Product ID').disabled).toBe(
      true,
    )
    fireEvent.change(screen.getByLabelText('Owner'), {
      target: { value: 'New owner' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Review draft' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByLabelText<HTMLInputElement>('Owner').value).toBe(
      'New owner',
    )
    expect(
      screen.getByLabelText<HTMLInputElement>('gold.batch_release_metrics')
        .checked,
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Review draft' }))
    await userEvent.click(
      screen.getByRole('button', { name: 'Save draft product' }),
    )
    expect(updateDataProduct).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        product_id: 'batch-record',
        name: 'Batch record',
        purpose: 'Original purpose',
        owner: 'New owner',
        classifications: ['internal'],
        member_dataset_ids: ['gold.batch_release_metrics'],
        expected_version: 4,
      },
    })
    expect(saved).toHaveBeenCalledWith(expect.objectContaining({ version: 5 }))
    expect(createDataProduct).not.toHaveBeenCalled()
  })
  it('uses actual relation selections and leaves authorization failure visible without creating a product', async () => {
    const created = vi.fn()
    vi.mocked(createDataProduct).mockRejectedValue(
      new Error('Your account does not have permission for this action.'),
    )
    render(
      <NewProduct
        env="sandbox"
        assets={assets}
        onCancel={() => {}}
        onCreated={created}
      />,
    )
    for (const [label, value] of [
      ['Product name', 'Batch record'],
      ['Product ID', 'batch-record'],
      ['Owner', 'QC Analytics'],
      ['Classification', 'internal'],
      ['Purpose and scope', 'Synthetic fixture scope.'],
    ])
      fireEvent.change(screen.getByLabelText(label), { target: { value } })
    fireEvent.click(screen.getByLabelText('gold.batch_release_metrics'))
    fireEvent.click(screen.getByRole('button', { name: 'Review draft' }))
    expect(createDataProduct).not.toHaveBeenCalled()
    expect(
      screen.getByText(/This creates only the collection declaration/),
    ).toBeTruthy()
    await userEvent.click(
      screen.getByRole('button', { name: 'Create draft product' }),
    )
    expect((await screen.findByRole('alert')).textContent).toContain(
      'does not have permission',
    )
    expect(created).not.toHaveBeenCalled()
    expect(createDataProduct).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        product_id: 'batch-record',
        name: 'Batch record',
        purpose: 'Synthetic fixture scope.',
        owner: 'QC Analytics',
        classifications: ['internal'],
        member_dataset_ids: ['gold.batch_release_metrics'],
      },
    })
  })
  it('disables review when no actual asset relations exist', () => {
    render(
      <NewProduct
        env="sandbox"
        assets={[]}
        onCancel={() => {}}
        onCreated={() => {}}
      />,
    )
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Review draft' })
        .disabled,
    ).toBe(true)
  })
})
