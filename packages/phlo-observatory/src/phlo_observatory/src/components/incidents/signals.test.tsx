// @vitest-environment jsdom
/** Verifies evidence claims and drilldowns across incident investigation types. */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { IncidentSignals } from './signals'
import type { ComponentProps } from 'react'

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    to,
    search,
  }: {
    children: React.ReactNode
    to: string
    search: Record<string, string>
  }) => <a href={`${to}?${new URLSearchParams(search)}`}>{children}</a>,
}))
afterEach(cleanup)
const props = (kind: string, evidence: Record<string, unknown> = {}) =>
  ({
    env: 'sandbox',
    incident: {
      kind,
      asset_id: 'silver/qc_results',
      description: 'Recorded source freshness failure',
    },
    evidence,
    runs: { items: [] },
    investigation: {
      kind: 'overview',
      asset: {
        relation: 'silver.qc_results',
        dependencies: [],
        freshness_status: 'fresh',
        downstream: [],
      },
    },
  }) as unknown as ComponentProps<typeof IncidentSignals>

describe('incident investigation signals', () => {
  it('shows recorded audit counts and scopes the query to the real relation', () => {
    render(
      <IncidentSignals
        {...props('audit', {
          check: 'within_spec',
          evaluation: {
            metadataEntries: [
              { label: 'failing_rows', intValue: 37 },
              { label: 'rows_checked', intValue: 96 },
            ],
          },
        })}
      />,
    )
    expect(screen.getByText('37 of 96')).toBeTruthy()
    expect(
      screen.getByRole('link', { name: 'Open in Query' }).getAttribute('href'),
    ).toContain('silver')
    expect(screen.getByText(/no failing-row sample/)).toBeTruthy()
  })
  it('shows actual merge revisions rather than fabricated per-column decisions', () => {
    render(
      <IncidentSignals
        {...props('merge', {
          source: 'fix-schema',
          target: 'main',
          status_code: 409,
          response: { message: 'Table changed on both branches' },
        })}
      />,
    )
    expect(screen.getByText('HTTP 409')).toBeTruthy()
    expect(screen.getByText('Table changed on both branches')).toBeTruthy()
    expect(
      screen
        .getByRole('link', { name: 'Compare and resolve branch' })
        .getAttribute('href'),
    ).toContain('branch=fix-schema')
  })
  it('separates current table freshness from the original source-reading incident', () => {
    render(<IncidentSignals {...props('freshness')} />)
    expect(screen.getByText('fresh')).toBeTruthy()
    expect(screen.getByText(/Source-reading age and table/)).toBeTruthy()
  })
  it('does not manufacture a historical baseline for slow loads', () => {
    render(
      <IncidentSignals
        {...props('performance', {
          elapsed_seconds: 7.31,
          fixture_threshold_seconds: 1,
        })}
      />,
    )
    expect(screen.getByText('7.3 s')).toBeTruthy()
    expect(screen.getByText(/historical baseline and step timing/)).toBeTruthy()
    expect(screen.getByText('No measured runs returned.')).toBeTruthy()
  })
})
