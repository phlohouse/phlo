/**
 * Tests Docker Compose label parsing and project matching used by service
 * discovery.
 */
import { describe, expect, it } from 'vitest'

import { getComposeLabelValue, matchesComposeProject } from './docker-labels'

describe('docker-labels', () => {
  const labels =
    'com.docker.compose.project=phlo-platform,com.docker.compose.service=dagster,foo=bar'

  it('extracts compose label values', () => {
    expect(getComposeLabelValue(labels, 'com.docker.compose.project')).toBe(
      'phlo-platform',
    )
    expect(getComposeLabelValue(labels, 'com.docker.compose.service')).toBe(
      'dagster',
    )
  })

  it('returns null for missing labels or keys', () => {
    expect(getComposeLabelValue(undefined, 'com.docker.compose.project')).toBe(
      null,
    )
    expect(
      getComposeLabelValue(labels, 'com.docker.compose.container-number'),
    ).toBe(null)
  })

  it('matches compose project when scoped', () => {
    expect(matchesComposeProject(labels, 'phlo-platform')).toBe(true)
    expect(matchesComposeProject(labels, 'pokemon-lakehouse')).toBe(false)
  })

  it('does not filter when compose project is unset', () => {
    expect(matchesComposeProject(labels, null)).toBe(true)
  })
})
