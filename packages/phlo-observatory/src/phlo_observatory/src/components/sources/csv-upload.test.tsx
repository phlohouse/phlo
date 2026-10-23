// @vitest-environment jsdom
/** Column review exposes full-file errors and never presents review as a completed load. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CsvUploadReview } from './csv-upload'
import { UploadInventory } from './upload-history'
import type * as UploadModule from '@/lib/data/api/uploads'
import type { CsvUpload } from '@/lib/data/api/uploads'
import type * as CandidateModule from '@/lib/data/api/upload-candidate'
import {
  archiveCsvUpload,
  reclaimCsvUpload,
  reviewCsvUpload,
} from '@/lib/data/api/uploads'
import {
  getUploadHandoff,
  reviewUploadCandidate,
} from '@/lib/data/api/upload-candidate'

vi.mock('@/lib/data/api/uploads', async (original) => ({
  ...(await original<typeof UploadModule>()),
  reviewCsvUpload: vi.fn(),
  archiveCsvUpload: vi.fn(),
  reclaimCsvUpload: vi.fn(),
}))
vi.mock('@/lib/data/api/upload-candidate', async (original) => ({
  ...(await original<typeof CandidateModule>()),
  reviewUploadCandidate: vi.fn(),
  getUploadHandoff: vi.fn(),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
const upload: CsvUpload = {
  upload_id: 'a'.repeat(32),
  env: 'sandbox',
  filename: 'results.csv',
  checksum: 'b'.repeat(64),
  byte_count: 10,
  actor: 'tester',
  created_at: '2026-10-22T12:00:00Z',
  version: 1,
  status: 'retained',
  columns: [
    {
      column: 'result',
      name: 'result',
      type: 'string',
      nullable: false,
      date_format: 'iso',
    },
  ],
  row_count: 26,
  parse_errors: [],
  error_count: 0,
  errors: [],
  reviewed_by: null,
  reviewed_at: null,
  raw_columns: ['result'],
  raw_rows: [{ result: 'ND' }],
  rows: [{ result: 'ND' }],
  sample_limit: 25,
  loaded: false,
}
describe('retained CSV review', () => {
  it('can resume confirmed interrupted cleanup from inventory without a broken file-detail link', async () => {
    vi.mocked(reclaimCsvUpload).mockResolvedValue({
      ...upload,
      reclaimed_at: '2026-10-23T12:00:00Z',
    })
    const changed = vi.fn()
    render(
      <UploadInventory
        env="sandbox"
        items={[
          {
            ...upload,
            archived_at: '2026-10-23T10:00:00Z',
            reclaim_started_at: '2026-10-23T11:00:00Z',
          },
        ]}
        total={1}
        archived
        onArchived={vi.fn()}
        onReclaimed={changed}
      />,
    )
    expect(screen.queryByRole('link', { name: upload.filename })).toBeNull()
    const user = userEvent.setup()
    await user.click(
      screen.getByRole('button', { name: 'Retry payload reclamation' }),
    )
    expect(
      screen.getByRole<HTMLButtonElement>('button', {
        name: 'Permanently reclaim',
      }).disabled,
    ).toBe(true)
    await user.type(
      screen.getByLabelText('Confirmation identity'),
      upload.upload_id,
    )
    await user.type(
      screen.getByLabelText('Reason'),
      'Resume interrupted local cleanup',
    )
    await user.click(
      screen.getByRole('button', { name: 'Permanently reclaim' }),
    )
    expect(reclaimCsvUpload).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        upload_id: upload.upload_id,
        confirmed: true,
        reason: 'Resume interrupted local cleanup',
        expected_checksum: upload.checksum,
      },
    })
    expect(changed).toHaveBeenCalledOnce()
  })
  it('reclaims only an archived exact-checksum upload after explicit confirmation and clears private row payload', async () => {
    vi.mocked(reclaimCsvUpload).mockResolvedValue({
      ...upload,
      archived_at: '2026-10-23T10:00:00Z',
      reclaimed_at: '2026-10-23T12:00:00Z',
      reclaimed_bytes: 10,
    })
    const reclaimed = vi.fn()
    render(
      <CsvUploadReview
        env="sandbox"
        initial={{ ...upload, archived_at: '2026-10-23T10:00:00Z' }}
        onRetained={vi.fn()}
        onReclaimed={reclaimed}
      />,
    )
    const user = userEvent.setup()
    await user.click(
      screen.getByRole('button', { name: 'Reclaim file payload' }),
    )
    await user.type(
      screen.getByLabelText('Confirmation identity'),
      upload.upload_id,
    )
    await user.type(
      screen.getByLabelText('Reason'),
      'Retention review complete',
    )
    await user.click(
      screen.getByRole('button', { name: 'Permanently reclaim' }),
    )
    expect(reclaimCsvUpload).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        upload_id: upload.upload_id,
        expected_checksum: upload.checksum,
        confirmed: true,
        reason: 'Retention review complete',
      },
    })
    expect(reclaimed).toHaveBeenCalledOnce()
    expect(screen.queryByText('First rows · saved review')).toBeNull()
  })
  it('clears reviewed eligibility and downloadable handoff after upload archive', async () => {
    const evidence = {
      stale: false,
      eligible_for_release: true,
      reason: null,
      base_hash: 'base',
      current_base_hash: 'base',
      candidate_hash: 'candidate',
      observed_candidate_hash: 'candidate',
    }
    vi.mocked(reviewUploadCandidate).mockResolvedValue({
      receipt: {
        env: 'sandbox',
        upload_id: upload.upload_id,
        actor: 'tester',
        review_version: 1,
        checksum: upload.checksum,
        recipe_digest: 'recipe',
        table: 'results',
        relation: 'bronze.results',
        asset_id: 'results',
        candidate_ref: 'sandbox-results',
        base_ref: 'main',
        base_hash: 'base',
        candidate_hash: 'candidate',
        launch_ref_hash: 'candidate',
        status: 'loaded',
        contract_digest: null,
        source_digest: null,
        plan_hash: null,
        job_name: null,
        job_snapshot_id: null,
        run_id: 'actual-run',
        repository_name: null,
        repository_location: null,
        snapshot_id: '123',
        row_count: 26,
        loaded: true,
        main_unchanged: true,
        files: [],
        candidate_binding: {},
        attempts: [],
      },
      evidence,
      preview: null,
      reconciliation: null,
    })
    vi.mocked(getUploadHandoff).mockResolvedValue({
      env: 'sandbox',
      kind: 'csv_candidate',
      source_review_required: true,
      automatic_promotion: false,
      files: [],
      candidate_evidence: evidence,
      release: {
        source_ref: null,
        target_ref: 'main',
        checks_url: null,
        trial_merge_url: null,
        merge_url: null,
        requires_signature: true,
        blocked_reason: null,
      },
    })
    vi.mocked(archiveCsvUpload).mockResolvedValue({
      ...upload,
      archived_at: '2026-10-23T12:00:00Z',
      archived_by: 'tester',
      archive_reason: 'Superseded',
    })
    render(
      <CsvUploadReview env="sandbox" initial={upload} onRetained={vi.fn()} />,
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Review recorded candidate' }),
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Prepare source handoff' }),
    )
    await screen.findByRole('button', { name: 'Download review bundle' })
    expect(screen.getAllByText(/Candidate evidence verified/)).toHaveLength(2)
    await userEvent.click(
      screen.getByRole('button', { name: 'Archive evidence' }),
    )
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Reason' }),
      'Superseded',
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Confirm archive' }),
    )
    await screen.findByRole('button', { name: 'Restore evidence' })
    expect(
      screen.queryByRole('button', { name: 'Download review bundle' }),
    ).toBeNull()
    expect(screen.queryByText(/Candidate evidence verified/)).toBeNull()
    expect(
      screen.getByRole('button', { name: 'Review recorded candidate' }),
    ).toBeTruthy()
  })
  it('accepts metadata-only archive responses while retaining the saved preview as read-only evidence', async () => {
    const {
      raw_columns: _rawColumns,
      raw_rows: _rawRows,
      rows: _rows,
      sample_limit: _sampleLimit,
      loaded: _loaded,
      ...summary
    } = upload
    vi.mocked(archiveCsvUpload).mockResolvedValue({
      ...summary,
      archived_at: '2026-10-23T10:00:00Z',
      archived_by: 'tester',
      archive_reason: 'Superseded review',
    })
    render(
      <CsvUploadReview env="sandbox" initial={upload} onRetained={vi.fn()} />,
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Archive evidence' }),
    )
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Reason' }),
      'Superseded review',
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Confirm archive' }),
    )
    expect(
      await screen.findByRole('button', { name: 'Restore evidence' }),
    ).toBeTruthy()
    expect(screen.getByText('First rows · saved review')).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Validate all 26 rows' }),
    ).toBeNull()
    expect(screen.getByText(/Restore before changing columns/)).toBeTruthy()
  })
  it('submits typed choices with the actual revision and shows errors beyond the preview', async () => {
    vi.mocked(reviewCsvUpload).mockResolvedValue({
      ...upload,
      version: 2,
      status: 'invalid',
      columns: [{ ...upload.columns[0], type: 'number' }],
      error_count: 1,
      errors: [
        { row: 27, column: 'result', message: 'Expected a finite number.' },
      ],
    })
    render(
      <CsvUploadReview env="sandbox" initial={upload} onRetained={() => {}} />,
    )
    await userEvent.click(screen.getByText('Edit all columns'))
    fireEvent.change(screen.getByLabelText('Type for result'), {
      target: { value: 'number' },
    })
    await userEvent.click(
      screen.getByRole('button', { name: 'Validate all 26 rows' }),
    )
    expect(
      await screen.findByText(/1 value errors across the complete file/),
    ).toBeTruthy()
    expect(screen.getByText(/Record 27/)).toBeTruthy()
    expect(reviewCsvUpload).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        upload_id: upload.upload_id,
        expected_version: 1,
        columns: [{ ...upload.columns[0], type: 'number', qualifiers: null }],
      },
    })
    expect(
      (
        await screen.findByRole<HTMLButtonElement>('button', {
          name: 'Prepare candidate destination',
        })
      ).disabled,
    ).toBe(true)
    expect(
      screen.getByText(
        /The base reference changes only through a separate reviewed merge/,
      ),
    ).toBeTruthy()
  })
  it('shows a validated review and offers destination preparation before loading', async () => {
    render(
      <CsvUploadReview
        env="sandbox"
        initial={{ ...upload, status: 'reviewed', reviewed_by: 'tester' }}
        onRetained={() => {}}
      />,
    )
    expect(screen.getByText('Columns validated')).toBeTruthy()
    expect(
      (
        await screen.findByRole<HTMLButtonElement>('button', {
          name: 'Prepare candidate destination',
        })
      ).disabled,
    ).toBe(false)
    expect(screen.queryByText('Loaded')).toBeNull()
  })
})

it('reviews detected UK dates and qualifiers explicitly, then previews saved derived values beside original text', async () => {
  const dateColumn = {
    column: 'pull_date',
    name: 'pull_date',
    type: 'string' as const,
    nullable: false,
    date_format: 'iso' as const,
  }
  const initial: CsvUpload = {
    ...upload,
    row_count: 3,
    columns: [upload.columns[0], dateColumn],
    raw_columns: ['result', 'pull_date'],
    raw_rows: [
      { result: '98.20', pull_date: '24/09/2026' },
      { result: 'ND', pull_date: '2026-09-24' },
      { result: '<LOQ', pull_date: '03/04/2026' },
    ],
    column_assistance: [
      {
        column: 'pull_date',
        dates: {
          iso: 1,
          day_first: 1,
          month_first: 0,
          ambiguous: 1,
          invalid: 0,
          examples: [
            {
              original: '24/09/2026',
              iso: null,
              day_first: '2026-09-24',
              month_first: null,
            },
            {
              original: '03/04/2026',
              iso: null,
              day_first: '2026-04-03',
              month_first: '2026-03-04',
            },
          ],
        },
        qualifiers: null,
      },
      {
        column: 'result',
        dates: null,
        qualifiers: { numbers: 1, nd: 1, below_loq: 1, other: 0, blank: 0 },
      },
    ],
  }
  vi.mocked(reviewCsvUpload).mockImplementation(({ data }) =>
    Promise.resolve({
      ...initial,
      status: 'reviewed',
      version: 2,
      columns: data.columns,
      rows: [
        {
          result: '98.20',
          result_value: 98.2,
          result_flag: null,
          pull_date: '2026-09-24',
        },
        {
          result: 'ND',
          result_value: null,
          result_flag: 'ND',
          pull_date: '2026-09-24',
        },
        {
          result: '<LOQ',
          result_value: null,
          result_flag: '<LOQ',
          pull_date: '2026-04-03',
        },
      ],
      output_columns: [
        {
          name: 'result',
          type: 'string',
          nullable: false,
          source_column: 'result',
          derived: false,
        },
        {
          name: 'result_value',
          type: 'number',
          nullable: true,
          source_column: 'result',
          derived: true,
        },
        {
          name: 'result_flag',
          type: 'string',
          nullable: true,
          source_column: 'result',
          derived: true,
        },
        {
          name: 'pull_date',
          type: 'date',
          nullable: false,
          source_column: 'pull_date',
          derived: false,
        },
      ],
    }),
  )
  render(
    <CsvUploadReview env="sandbox" initial={initial} onRetained={() => {}} />,
  )
  const user = userEvent.setup()
  expect(screen.getByText(/1 dates are ambiguous/)).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'Day first (UK)' }))
  expect(screen.getByText('24/09/2026 → 2026-09-24')).toBeTruthy()
  await user.click(
    screen.getByRole('button', {
      name: 'Keep text, add result_value and result_flag',
    }),
  )
  await user.click(screen.getByRole('button', { name: 'Validate all 3 rows' }))
  expect(reviewCsvUpload).toHaveBeenCalledWith({
    data: expect.objectContaining({
      expected_version: 1,
      columns: [
        expect.objectContaining({
          column: 'result',
          type: 'string',
          qualifiers: { value_name: 'result_value', flag_name: 'result_flag' },
        }),
        expect.objectContaining({
          column: 'pull_date',
          type: 'date',
          date_format: 'day_first',
        }),
      ],
    }),
  })
  expect(
    await screen.findByRole('columnheader', { name: /result_value\s*number/ }),
  ).toBeTruthy()
  expect(
    screen.getByRole('columnheader', { name: /result_flag\s*string/ }),
  ).toBeTruthy()
  expect(screen.getByText('98.20')).toBeTruthy()
  expect(screen.getByText('98.2')).toBeTruthy()
  expect(screen.queryByText('Candidate loaded')).toBeNull()
})

it('keeps the retained file and full column matrix compact until their accessible disclosures are opened', async () => {
  render(
    <CsvUploadReview env="sandbox" initial={upload} onRetained={() => {}} />,
  )
  const user = userEvent.setup()
  const matrix = screen.getByText('Edit all columns').closest('details')
  expect(matrix?.hasAttribute('open')).toBe(false)
  expect(screen.queryByLabelText('Replace retained file')).toBeNull()
  await user.click(screen.getByRole('button', { name: 'Replace file' }))
  expect(screen.getByLabelText('Replace retained file')).toBeTruthy()
  expect(
    screen
      .getByRole('button', { name: 'Cancel replacement' })
      .getAttribute('aria-expanded'),
  ).toBe('true')
  await user.click(screen.getByRole('button', { name: 'Cancel replacement' }))
  expect(screen.queryByLabelText('Replace retained file')).toBeNull()
  await user.click(screen.getByText('Edit all columns'))
  expect(matrix?.hasAttribute('open')).toBe(true)
  expect(screen.getByLabelText('Type for result')).toBeTruthy()
  expect(
    screen.getByRole('button', { name: 'Validate all 26 rows' }),
  ).toBeTruthy()
  expect(screen.getByText('ND')).toBeTruthy()
  expect(reviewCsvUpload).not.toHaveBeenCalled()
})
