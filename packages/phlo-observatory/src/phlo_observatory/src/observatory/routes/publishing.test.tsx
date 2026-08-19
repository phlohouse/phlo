import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const publishingRoute = readFileSync(
  resolve(import.meta.dirname, '../../routes/publishing.tsx'),
  'utf8',
)

describe('Publishing readiness requests', () => {
  it('loads promoted dataset readiness with one bulk request', () => {
    expect(publishingRoute).toContain(
      'getObservatoryPublishingReadinessDirect()',
    )
    expect(publishingRoute).toContain('item.dataset_id')
    expect(publishingRoute).not.toContain('getObservatoryDatasetProfileDirect')
    expect(publishingRoute).not.toContain('Promise.all(')
  })
})
