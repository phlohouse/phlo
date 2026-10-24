// @vitest-environment jsdom
/** Verifies scoped row-query behaviour and honest current-data evidence. */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuditRows, incidentFailureSql } from './audit-rows'
import { getQuerySession, submitQuery } from '@/lib/data/api/query'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="/query">{children}</a>
  ),
}))
vi.mock('@/lib/data/api/query', () => ({
  submitQuery: vi.fn(),
  getQuerySession: vi.fn(),
}))
afterEach(cleanup)
beforeEach(() => vi.resetAllMocks())
const sql = 'SELECT * FROM silver.qc_results WHERE in_spec = FALSE'
const completed = {
  id: 'actual-query',
  env: 'sandbox',
  nessie_ref: 'main',
  status: 'completed',
  engine: 'trino',
  evidence_available: true,
  sql_hash: 'hash',
  created_at: '2026-10-22T12:00:00Z',
  updated_at: '2026-10-22T12:00:01Z',
  error: null,
  result: {
    columns: [
      { name: 'sample_id', type: 'varchar' },
      { name: 'batch_id', type: 'varchar' },
      { name: 'result_value', type: 'double' },
    ],
    rows: Array.from({ length: 9 }, (_, index) => ({
      sample_id: `S-${index}`,
      batch_id: index < 5 ? 'B-1' : 'B-2',
      result_value: 85,
    })),
    has_more: false,
  },
} as const

describe('audit failing-row query', () => {
  it('generates only the known boolean check predicate and quotes identifiers', () => {
    expect(
      incidentFailureSql(
        'silver.qc_results',
        [
          { name: 'in_spec', type: 'BOOLEAN' },
          { name: 'sample_id', type: 'varchar' },
        ],
        'within_spec',
      ),
    ).toBe(
      'SELECT *\nFROM "silver"."qc_results"\nWHERE "in_spec" = FALSE\nORDER BY "sample_id"\nLIMIT 100',
    )
    expect(
      incidentFailureSql(
        'silver.qc_results',
        [{ name: 'in_spec', type: 'varchar' }],
        'within_spec',
      ),
    ).toBeUndefined()
    expect(
      incidentFailureSql(
        'silver.qc_results',
        [{ name: 'in_spec', type: 'boolean' }],
        'other_check',
      ),
    ).toBeUndefined()
  })
  it('runs only on request, shows eight actual rows, and groups the returned failures', async () => {
    vi.mocked(submitQuery).mockResolvedValue(completed as never)
    render(<AuditRows env="sandbox" sql={sql} />)
    expect(submitQuery).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Load failing rows' }))
    await waitFor(() =>
      expect(screen.getByText('9 rows · showing 8')).toBeTruthy(),
    )
    expect(submitQuery).toHaveBeenCalledWith({ data: { env: 'sandbox', sql } })
    expect(screen.getAllByRole('row')).toHaveLength(9)
    expect(screen.queryByText('S-8')).toBeNull()
    expect(screen.getByText('5')).toBeTruthy()
    expect(screen.getByText('4')).toBeTruthy()
    expect(screen.getByText(/actual-query/)).toBeTruthy()
    expect(
      screen.getByText(/may differ from the rows at incident/),
    ).toBeTruthy()
    expect(getQuerySession).not.toHaveBeenCalled()
  })
  it('keeps permission errors visible without substituting fixture rows', async () => {
    vi.mocked(submitQuery).mockRejectedValue(
      new Error('dataset.query permission required'),
    )
    render(<AuditRows env="sandbox" sql={sql} />)
    fireEvent.click(screen.getByRole('button', { name: 'Load failing rows' }))
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain(
        'dataset.query permission required',
      ),
    )
    expect(screen.queryByRole('table')).toBeNull()
  })
})
