// Prevent asset inventory from being presented as a governed Dataset contract.
import { describe, expect, it } from 'vitest'

import { datasetV1Unavailable } from './datasetsV1'

describe('Dataset v1 boundary', () => {
  it('marks both Dataset surfaces unavailable rather than mapping assets into Datasets', () => {
    expect(datasetV1Unavailable('list')).toEqual({
      kind: 'unavailable',
      message: expect.stringContaining('no Dataset contract'),
    })
    expect(datasetV1Unavailable('detail')).toEqual({
      kind: 'unavailable',
      message: expect.stringContaining('no Dataset contract'),
    })
  })
})
