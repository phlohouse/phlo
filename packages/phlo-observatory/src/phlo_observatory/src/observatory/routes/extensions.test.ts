/**
 * Verifies extension-contributed nav items and routes stay explicit and that
 * legacy aliases are never applied.
 */
import { describe, expect, it } from 'vitest'

import type { ObservatoryExtension } from '@/observatory/api/types'

import { contributedNav, contributedRoute } from '@/routes/extensions'

describe('extension route contributions', () => {
  it('keeps extension routes explicit and does not apply legacy aliases', () => {
    const extension: ObservatoryExtension = {
      id: 'canonical-extension',
      name: 'Canonical extension',
      version: '1.0.0',
      enabled: true,
      nav: ['/lineage', '/datasets', '/tables', '/datasets'],
      routes: ['/lineage', '/datasets', '/tables'],
      settings_scope: null,
      metadata: {},
    }

    expect(contributedNav(extension)).toEqual([
      '/lineage',
      '/datasets',
      '/tables',
    ])
    expect(extension.routes.map(contributedRoute)).toEqual([
      '/lineage',
      '/datasets',
      '/tables',
    ])
  })
})
