/** Verifies combined overview service filters, including unobserved services. */
import { describe, expect, it } from 'vitest'
import { filterServices } from '@/routes/_app/index'

const services = [
  {
    id: 'Asset API',
    status: 'healthy',
    observed_at: '2026-10-02T10:00:00Z',
    response_time_seconds: 0.12,
  },
  {
    id: 'Run Worker',
    status: 'unknown',
    observed_at: null,
    response_time_seconds: null,
  },
] satisfies Parameters<typeof filterServices>[0]

describe('overview service filters', () => {
  it('combines trimmed, case-insensitive names with exact statuses', () => {
    expect(filterServices(services, '  RUN ', 'unknown')).toEqual([services[1]])
    expect(filterServices(services, 'run', 'healthy')).toEqual([])
  })

  it('keeps unobserved entries when filters are cleared', () => {
    expect(filterServices(services, '', '')).toEqual(services)
    expect(filterServices(services, '', 'healthy')).toEqual([services[0]])
  })
})
