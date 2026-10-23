// @vitest-environment jsdom
/** Filters real evidence and keeps unknown counts and private receipt identity visible. */
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoadsInventory } from './loads'
import type { LoadObservation, LoadsPage } from '@/lib/data/api/loads'
import { getLoads } from '@/lib/data/api/loads'

vi.mock('@/lib/data/api/loads', () => ({ getLoads: vi.fn() }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="#evidence">{children}</a>
  ),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
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
  next_cursor: null,
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
  it('continues through empty pages, preserves loaded filters and retries the same failed cursor', async () => {
    vi.mocked(getLoads)
      .mockResolvedValueOnce({ ...data, items: [], next_cursor: 'scan-next' })
      .mockRejectedValueOnce(new Error('Provider temporarily unavailable'))
      .mockResolvedValueOnce({
        ...data,
        items: [{ ...base, id: 'older', name: 'older-results.csv' }],
      })
    render(<LoadsInventory data={{ ...data, next_cursor: 'first-next' }} />)
    fireEvent.change(screen.getByLabelText('Find a file, table or run'), {
      target: { value: 'older' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Load more observations' }),
    )
    await screen.findByText(/loaded across 2 pages/)
    expect(
      screen.getByText('No authorized observations match these filters.'),
    ).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', { name: 'Load more observations' }),
    )
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Provider temporarily unavailable',
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry loading observations' }),
    )
    expect(
      await screen.findByRole('button', { name: /older-results.csv/ }),
    ).toBeTruthy()
    expect(
      screen.getByText(/1 visible observations from 3 loaded across 3 pages/),
    ).toBeTruthy()
    expect(
      screen.getByText(/Filters and export apply only to loaded pages/),
    ).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Load more observations' }),
    ).toBeNull()
    expect(getLoads).toHaveBeenNthCalledWith(2, {
      data: { env: 'sandbox', after: 'scan-next' },
    })
    expect(getLoads).toHaveBeenNthCalledWith(3, {
      data: { env: 'sandbox', after: 'scan-next' },
    })
  })
  it('does not append a late old-environment page to the new inventory', async () => {
    let finish!: (page: LoadsPage) => void
    vi.mocked(getLoads).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const view = render(
      <LoadsInventory data={{ ...data, next_cursor: 'old-cursor' }} />,
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Load more observations' }),
    )
    expect(
      screen.getByRole<HTMLButtonElement>('button', {
        name: 'Loading observations…',
      }).disabled,
    ).toBe(true)
    view.rerender(
      <LoadsInventory
        data={{
          ...data,
          env: 'qa',
          items: [{ ...base, name: 'qa-results.csv' }],
        }}
      />,
    )
    await act(() =>
      finish({
        ...data,
        items: [{ ...base, id: 'late', name: 'late-old-results.csv' }],
      }),
    )
    expect(screen.getByRole('button', { name: /qa-results.csv/ })).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: /late-old-results.csv/ }),
    ).toBeNull()
    expect(
      screen.getByText(/1 visible observations from 1 loaded across 1 page/),
    ).toBeTruthy()
  })
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
  it('keeps upload creation separate from unknown load time and actual run timing', () => {
    render(
      <LoadsInventory
        data={{
          ...data,
          items: [
            {
              ...base,
              name: 'opaque-receipt',
              upload_filename: 'original-results.csv',
              upload_created_at: '2026-10-20T10:00:00Z',
              upload_actor: 'quality-owner',
              timestamp_basis: 'unavailable',
              run_started_at: null,
              run_ended_at: null,
            },
          ],
        }}
      />,
    )
    const evidence = within(
      screen.getByRole('complementary', { name: 'Selected load evidence' }),
    )
    expect(
      screen.getByRole('button', { name: /original-results.csv/ }),
    ).toBeTruthy()
    expect(evidence.getByText('quality-owner')).toBeTruthy()
    expect(
      evidence.getByText('Uploaded at (not load time)').nextElementSibling
        ?.textContent,
    ).toBe('2026-10-20T10:00:00Z')
    for (const label of ['Observation timestamp', 'Run started', 'Run ended'])
      expect(evidence.getByText(label).nextElementSibling?.textContent).toBe(
        'Not recorded',
      )
  })
})
