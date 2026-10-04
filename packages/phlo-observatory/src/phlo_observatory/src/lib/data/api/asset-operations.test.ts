/** Checks materialization intent at the server-function trust boundary. */
import { describe, expect, it } from 'vitest'
import { materializationInputSchema } from './assets'

const intent = {
  env: 'staging',
  id: 'orders',
  job_name: 'orders_job',
  mode: 'backfill',
  write_ref: 'staging-orders',
  rebuild_downstream: true,
  from_time: '2026-09-25T00:00:00Z',
  to_time: '2026-09-27T00:00:00Z',
}
describe('materialization intent', () => {
  it('preserves the asymmetric range, target and downstream choice', () => {
    expect(materializationInputSchema.parse(intent)).toEqual(intent)
  })
  it('rejects absent, reversed, equal and timezone-less ranges', () => {
    for (const change of [
      { from_time: undefined },
      { to_time: undefined },
      { to_time: '2026-09-24T00:00:00Z' },
      { to_time: intent.from_time },
      { from_time: '2026-09-25T00:00:00' },
    ])
      expect(
        materializationInputSchema.safeParse({ ...intent, ...change }).success,
      ).toBe(false)
  })
  it('accepts incremental and full loads only without a backfill window', () => {
    for (const mode of ['latest', 'full']) {
      expect(
        materializationInputSchema.safeParse({ ...intent, mode }).success,
      ).toBe(false)
      expect(
        materializationInputSchema.safeParse({
          ...intent,
          mode,
          from_time: undefined,
          to_time: undefined,
        }).success,
      ).toBe(true)
    }
  })
  it('rejects unknown environments, modes and non-boolean rebuild controls', () => {
    for (const change of [
      { env: 'dev' },
      { mode: 'drop' },
      { rebuild_downstream: 'false' },
    ])
      expect(
        materializationInputSchema.safeParse({ ...intent, ...change }).success,
      ).toBe(false)
  })
})
