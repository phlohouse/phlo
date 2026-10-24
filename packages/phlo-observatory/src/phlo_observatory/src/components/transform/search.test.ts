/** Human-readable and router-encoded focus links resolve to the same safe state. */
import { describe, expect, it } from 'vitest'
import { transformSearchSchema } from './search'

describe('Transform focus search', () => {
  it.each([0, 1, '0', '1'])(
    'accepts actual numeric or encoded focus %s',
    (focus) => {
      expect(transformSearchSchema.parse({ focus }).focus).toBe(String(focus))
    },
  )
  it('defaults ordinary model and pinned revision links to workspace mode', () => {
    expect(
      transformSearchSchema.parse({
        model: 'review.bioreactor_runs_1min',
        draft: '07171e9b-013b-4111-9384-c7afddfec767',
        revision: 2,
      }).focus,
    ).toBe('0')
  })
  it.each([2, -1, true, 'true', '01', {}, null])(
    'rejects unsupported focus values %s',
    (focus) => {
      expect(transformSearchSchema.safeParse({ focus }).success).toBe(false)
    },
  )
})
