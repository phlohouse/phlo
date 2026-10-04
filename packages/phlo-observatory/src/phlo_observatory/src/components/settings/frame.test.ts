/** Checks settings navigation keeps only search state accepted by each destination. */
import { describe, expect, it } from 'vitest'
import { settingsDestination } from './frame'

describe('settings destinations', () => {
  it('switches to the intended environment without forwarding the source environment', () => {
    expect(
      settingsDestination(
        { label: 'staging', to: '/staging', env: 'staging' },
        { env: 'prod' },
      ),
    ).toEqual({ to: '/staging', hash: undefined, search: { env: 'staging' } })
    expect(
      settingsDestination(
        { label: 'prod', to: '/', env: 'prod' },
        { env: 'staging' },
      ),
    ).toEqual({ to: '/', hash: undefined, search: { env: 'prod' } })
  })

  it('does not forward audit-only search parameters into settings routes', () => {
    const auditSearch = { env: 'prod', range: 'week', signed: false }
    expect(
      settingsDestination(
        { label: 'Members', to: '/settings/members' },
        auditSearch,
      ),
    ).toEqual({
      to: '/settings/members',
      hash: undefined,
      search: { env: 'prod' },
    })
  })
})
