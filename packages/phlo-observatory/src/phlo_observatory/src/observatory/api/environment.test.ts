// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'

import {
  selectEnvironment,
  selectedEnvironment,
  v1Endpoint,
} from './environment'

afterEach(() => window.localStorage?.clear())

describe('explicit environment selection', () => {
  it('does not invent a default environment', () => {
    expect(selectedEnvironment()).toBeNull()
    expect(() => v1Endpoint('/api/v1/runs')).toThrow('Select prod or staging')
  })

  it('adds exactly one selected environment to v1 requests', () => {
    selectEnvironment('staging')
    expect(v1Endpoint('/api/v1/runs?limit=100')).toBe(
      '/api/v1/runs?limit=100&env=staging',
    )
  })
})
