// @vitest-environment jsdom
/** Filters real evidence and keeps unknown counts and private receipt identity visible. */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoadsInventory } from './loads'
import type { LoadObservation, LoadsPage } from '@/lib/data/api/loads'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="#evidence">{children}</a>
  ),
}))
afterEach(cleanup)
const base: LoadObservation = {
  id: 'csv-success',
  evidence_kind: 'csv_candidate',
  name: 'lims.csv',
  source: 'Reviewed CSV',
  asset_id: 'dlt_review',
  relation: 'bronze.review',
  status: 'loaded',
  observed_at: null,
  rows: 96,
  rows_basis: 'Verified candidate table total',
  byte_count: 9774,
  checksum: 'exact-checksum',
  run_id: 'actual-candidate-run',
  job_id: '__ASSET_JOB',
  ref: 'sandbox-upload-owned',
  ref_hash: 'actual-hash',
  snapshot_id: '9007199254740993',
  upload_id: 'own-upload',
  review_version: 2,
  recipe_digest: 'frozen-recipe',
  detail: 'Recorded private receipt; main is separate.',
}
const data: LoadsPage = {
  env: 'sandbox',
  observed_at: '2026-10-22T20:00:00Z',
  truncated: false,
  limitations: ['Bounded observations; no invented source packages.'],
  items: [
    base,
    {
      ...base,
      id: 'run-failed',
      evidence_kind: 'ingestion_run',
      name: 'Historian',
      source: 'Process historian',
      status: 'run_failure',
      rows: null,
      rows_basis: null,
      byte_count: null,
      checksum: null,
      upload_id: null,
      run_id: 'failed-run',
      detail: 'Run failure does not establish rejected rows.',
    },
  ],
}
describe('Loads inventory', () => {
  it('opens the actual loaded receipt, preserves exact snapshot and avoids cross-grain totals', () => {
    render(<LoadsInventory data={data} />)
    const aside = screen.getByRole('complementary', {
      name: 'Selected load evidence',
    })
    expect(within(aside).getByText('9007199254740993')).toBeTruthy()
    expect(within(aside).getByText('actual-candidate-run')).toBeTruthy()
    expect(
      within(aside).getByRole('link', { name: 'Open private upload' }),
    ).toBeTruthy()
    expect(within(aside).queryByRole('link', { name: 'Open run' })).toBeNull()
    expect(
      screen.getByText(/their row counts are not added together/),
    ).toBeTruthy()
  })
  it('filters by status and source, showing absent failure counts as unknown', () => {
    render(<LoadsInventory data={data} />)
    fireEvent.change(screen.getByLabelText('Status'), {
      target: { value: 'run_failure' },
    })
    expect(screen.getByText(/1 visible observations/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /lims.csv/ })).toBeNull()
    const aside = screen.getByRole('complementary', {
      name: 'Selected load evidence',
    })
    expect(within(aside).getByText('failed-run')).toBeTruthy()
    expect(within(aside).getAllByText('Not recorded').length).toBeGreaterThan(0)
    expect(within(aside).getByRole('link', { name: 'Open run' })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Source'), {
      target: { value: 'Reviewed CSV' },
    })
    expect(
      screen.getByText('No authorized observations match these filters.'),
    ).toBeTruthy()
  })
  it('searches actual run and reference identifiers and exports only visible observations', () => {
    const create = vi.fn(() => 'blob:local')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})
    render(<LoadsInventory data={data} />)
    fireEvent.change(screen.getByLabelText('Find a file, table or run'), {
      target: { value: 'actual-candidate-run' },
    })
    expect(screen.getByText(/1 visible observations/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Export observations' }))
    expect(create).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
    expect(revoke).toHaveBeenCalledWith('blob:local')
    click.mockRestore()
    vi.unstubAllGlobals()
  })
})
