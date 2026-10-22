/** Product declarations keep draft identity separate from Dataset publication and SQL execution. */
import { describe, expect, it } from 'vitest'
import { productInputSchema, productQuery } from './data-products'

const input = {
  env: 'sandbox',
  product_id: 'batch-record-cgt04',
  name: 'CGT-04 batch record',
  purpose: 'Synthetic fixture release evidence.',
  owner: 'QC Analytics',
  classifications: ['internal'],
  member_dataset_ids: ['gold.batch_release_metrics', 'silver.qc_results'],
}
describe('data product boundaries', () => {
  it('accepts distinct members and rejects publication/access fields and duplicate membership', () => {
    expect(productInputSchema.parse(input)).toEqual(input)
    expect(
      productInputSchema.safeParse({ ...input, publication_state: 'published' })
        .success,
    ).toBe(false)
    expect(
      productInputSchema.safeParse({ ...input, grants: ['aster-bio'] }).success,
    ).toBe(false)
    expect(
      productInputSchema.safeParse({
        ...input,
        member_dataset_ids: ['gold.one', 'gold.one'],
      }).success,
    ).toBe(false)
    expect(
      productInputSchema.safeParse({ ...input, member_dataset_ids: [] })
        .success,
    ).toBe(false)
    expect(
      productInputSchema.safeParse({ ...input, product_id: '../outside' })
        .success,
    ).toBe(false)
  })
  it('quotes each actual relation identifier and builds a bounded query without executing it', () => {
    expect(productQuery('gold.batch_release_metrics')).toBe(
      'SELECT * FROM "gold"."batch_release_metrics" LIMIT 100',
    )
    expect(productQuery('gold.odd"table')).toBe(
      'SELECT * FROM "gold"."odd""table" LIMIT 100',
    )
  })
})
