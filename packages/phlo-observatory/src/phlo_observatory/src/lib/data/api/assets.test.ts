/** Verifies typed preview inputs and explicit layer evidence at the asset boundary. */
import { describe, expect, it } from 'vitest'
import { assetLayer, previewFilterSchema } from './assets'

describe('Asset read inputs', () => {
  it('retains literal injection text but rejects SQL operators and hidden selectors', () => {
    const filter = {
      column: 'odd"column',
      operator: 'eq',
      value: "O'Brien'; DROP TABLE events; --",
    }
    expect(previewFilterSchema.parse(filter)).toEqual(filter)
    expect(
      previewFilterSchema.safeParse({ ...filter, operator: 'eq OR TRUE' })
        .success,
    ).toBe(false)
    expect(
      previewFilterSchema.safeParse({ ...filter, env: 'prod' }).success,
    ).toBe(false)
    expect(
      previewFilterSchema.safeParse({ ...filter, value: 9007199254740992 })
        .success,
    ).toBe(false)
    expect(
      previewFilterSchema.safeParse({ ...filter, value: Infinity }).success,
    ).toBe(false)
    expect(
      previewFilterSchema.safeParse({
        column: 'site',
        operator: 'is_null',
        value: 'ignored',
      }).success,
    ).toBe(false)
    expect(
      previewFilterSchema.parse({ column: 'site', operator: 'is_null' }),
    ).toEqual({ column: 'site', operator: 'is_null' })
  })

  it('prefers a declared layer without changing the group name', () => {
    const asset = {
      group_name: 'bronze',
      key: ['silver', 'events'],
      layer: 'gold' as const,
    }
    expect(assetLayer(asset)).toBe('gold')
    expect(asset.group_name).toBe('bronze')
    expect(assetLayer({ group_name: 'misc', key: ['silver', 'events'] })).toBe(
      'silver',
    )
    expect(assetLayer({ group_name: 'misc', key: ['events'] })).toBeUndefined()
  })
})
