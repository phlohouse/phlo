/** Validates old saved queries and strict presentation metadata. */
import { describe, expect, it } from 'vitest'
import {
  queryViewSchema,
  restoreQueryView,
  savedQueryMetadata,
} from './query-view'

describe('saved query views', () => {
  it('opens legacy queries with the table view and default builder settings', () => {
    expect(restoreQueryView({})).toMatchObject({
      view: 'results',
      type: 'line',
      operation: 'avg',
      bucket: 'raw',
    })
  })
  it('round trips chart and pivot settings through saved metadata', () => {
    const view = queryViewSchema.parse({
      view: 'pivot',
      x: 'week',
      y: 'potency',
      group: 'product',
      operation: 'max',
      type: 'bar',
      bucket: 'day',
      reference: true,
      lower: '80',
      upper: '125',
      labels: true,
    })
    expect(restoreQueryView(savedQueryMetadata(view))).toEqual(view)
  })
  it.each([
    { version: 2 },
    { type: 'execute' },
    { reference: 'true' },
    { unknown: true },
    { group: 'x'.repeat(257) },
  ])('rejects invalid or unknown config: %j', (config) => {
    expect(restoreQueryView({ query_view: config })).toEqual(restoreQueryView())
    expect(() => savedQueryMetadata(config as never)).toThrow()
  })
})
