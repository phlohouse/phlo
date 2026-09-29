import { describe, expect, it } from 'vitest'

import type {
  ObservatoryAsset,
  ObservatoryLogEvent,
} from '@/observatory/api/types'
import { latestRunLogsForAsset } from '@/routes/lineage'

const asset: ObservatoryAsset = {
  id: 'orders',
  name: 'orders',
  kinds: [],
  dependencies: [],
  resources: [],
  checks: [],
  metadata: { last_run_id: 'run-2' },
}

const logs: Array<ObservatoryLogEvent> = [
  {
    id: 'run-1:event-1',
    level: 'info',
    message: 'unrelated run',
    metadata: { run_id: 'run-1' },
  },
  {
    id: 'run-2:event-1',
    level: 'info',
    message: 'latest asset run',
    metadata: { run_id: 'run-2' },
  },
]

describe('lineage run evidence', () => {
  it('shows only run logs matching the asset latest-run reference', () => {
    expect(latestRunLogsForAsset(asset, logs)).toEqual([logs[1]])
  })

  it('does not attach run logs when the asset has no latest-run reference', () => {
    expect(latestRunLogsForAsset({ ...asset, metadata: {} }, logs)).toEqual([])
  })
})
