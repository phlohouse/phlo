// @vitest-environment jsdom
/** Destination confirms a pinned candidate plan and waits for real readback before claiming loaded. */
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CsvDestination } from './csv-destination'
import type { CsvUpload } from '@/lib/data/api/uploads'
import type { UploadCandidate } from '@/lib/data/api/upload-candidate'
import {
  getUploadCandidate,
  loadUploadCandidate,
  planUploadCandidate,
  prepareUploadDestination,
  previewUploadCandidate,
} from '@/lib/data/api/upload-candidate'

vi.mock('@/lib/data/api/upload-candidate', () => ({
  getUploadCandidate: vi.fn(),
  prepareUploadDestination: vi.fn(),
  planUploadCandidate: vi.fn(),
  loadUploadCandidate: vi.fn(),
  previewUploadCandidate: vi.fn(),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
const upload: CsvUpload = {
  upload_id: 'a'.repeat(32),
  env: 'sandbox',
  filename: 'batch.csv',
  checksum: 'b'.repeat(64),
  byte_count: 20,
  actor: 'alice',
  created_at: '2026-10-22T12:00:00Z',
  version: 4,
  status: 'reviewed',
  columns: [],
  output_columns: [
    {
      name: 'result',
      type: 'string',
      nullable: false,
      source_column: 'result',
      derived: false,
    },
  ],
  row_count: 96,
  parse_errors: [],
  error_count: 0,
  errors: [],
  reviewed_by: 'alice',
  reviewed_at: '2026-10-22T12:01:00Z',
  raw_columns: [],
  raw_rows: [],
  rows: [],
  sample_limit: 25,
  loaded: false,
}
const prepared: UploadCandidate = {
  upload_id: upload.upload_id,
  env: 'sandbox',
  actor: 'alice',
  review_version: 4,
  checksum: upload.checksum,
  recipe_digest: 'c'.repeat(64),
  table: 'upload_aaaaaaaaaaaa',
  relation: 'bronze.upload_aaaaaaaaaaaa',
  asset_id: 'dlt_upload_aaaaaaaaaaaa',
  candidate_ref: `sandbox-upload-${upload.upload_id}`,
  base_ref: 'main',
  base_hash: 'base',
  candidate_hash: 'base',
  launch_ref_hash: null,
  status: 'prepared',
  contract_digest: 'contract',
  source_digest: 'source',
  plan_hash: null,
  job_name: null,
  job_snapshot_id: null,
  run_id: null,
  repository_name: null,
  repository_location: 'sandbox_jobs',
  snapshot_id: null,
  row_count: null,
  loaded: false,
  main_unchanged: null,
  files: [],
  candidate_binding: {},
  attempts: [],
}
describe('reviewed CSV candidate destination', () => {
  it('keeps destination unavailable while column choices are unsaved', async () => {
    vi.mocked(getUploadCandidate).mockRejectedValue(new Error('not prepared'))
    render(<CsvDestination env="sandbox" upload={upload} dirty />)
    expect(
      (
        await screen.findByRole<HTMLButtonElement>('button', {
          name: 'Prepare candidate destination',
        })
      ).disabled,
    ).toBe(true)
    expect(screen.queryByText('Candidate loaded')).toBeNull()
  })
  it('requires actual preparation, estimate and confirmation before showing observed candidate rows', async () => {
    vi.mocked(getUploadCandidate).mockRejectedValue(new Error('not prepared'))
    vi.mocked(prepareUploadDestination).mockResolvedValue(prepared)
    const planned = {
      ...prepared,
      status: 'planned' as const,
      plan_hash: 'verified-plan',
      job_name: 'one_off',
      job_snapshot_id: 'job-revision',
      launch_ref_hash: 'base',
    }
    vi.mocked(planUploadCandidate).mockResolvedValue(planned)
    const queued = { ...planned, status: 'queued' as const, run_id: 'real-run' }
    vi.mocked(loadUploadCandidate).mockResolvedValue(queued)
    const loaded = {
      ...queued,
      status: 'loaded' as const,
      loaded: true,
      row_count: 96,
      snapshot_id: '9223372036854775801',
      candidate_hash: 'written',
      base_ref: 'release_test',
      main_unchanged: true,
    }
    vi.mocked(previewUploadCandidate).mockResolvedValue({
      receipt: loaded,
      columns: ['result'],
      column_types: ['varchar'],
      rows: [{ result: 'ND' }],
      has_more: true,
      row_count: 96,
      snapshot_id: '9223372036854775801',
      ref_hash: 'written',
      query_id: 'actual-query',
    })
    render(<CsvDestination env="sandbox" upload={upload} dirty={false} />)
    const user = userEvent.setup()
    await user.click(
      await screen.findByRole('button', {
        name: 'Prepare candidate destination',
      }),
    )
    expect(prepareUploadDestination).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        upload_id: upload.upload_id,
        expected_version: 4,
        table: prepared.table,
      },
    })
    await user.click(
      screen.getByRole('button', { name: 'Estimate one-off load' }),
    )
    await user.click(
      screen.getByRole('button', { name: 'Confirm load into candidate' }),
    )
    expect(loadUploadCandidate).toHaveBeenCalledWith({
      data: expect.objectContaining({
        expected_version: 4,
        recipe_digest: prepared.recipe_digest,
        plan_hash: 'verified-plan',
        confirmed: true,
      }),
    })
    expect(screen.queryByText('Candidate loaded')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Check load status' }))
    expect(await screen.findByText('Candidate loaded')).toBeTruthy()
    expect(screen.getByText('ND')).toBeTruthy()
    expect(screen.getByText(/9223372036854775801/)).toBeTruthy()
    expect(
      screen.getByText(/Base reference release_test is unchanged/),
    ).toBeTruthy()
  })
})

it('refreshes a failed receipt and requires an explicit retry confirmation for the archived run', async () => {
  const failed = {
    ...prepared,
    status: 'failed' as const,
    run_id: 'failed-run',
    plan_hash: 'first-plan',
    job_snapshot_id: 'job-revision',
    launch_ref_hash: 'base',
  }
  vi.mocked(getUploadCandidate).mockResolvedValue(failed)
  const attempts = [
    {
      run_id: 'failed-run',
      status: 'FAILURE',
      plan_hash: 'first-plan',
      job_snapshot_id: 'job-revision',
      launch_ref_hash: 'base',
    },
  ]
  const replanned = {
    ...failed,
    status: 'planned' as const,
    run_id: null,
    plan_hash: 'retry-plan',
    attempts,
  }
  vi.mocked(planUploadCandidate).mockResolvedValue(replanned)
  vi.mocked(loadUploadCandidate).mockResolvedValue({
    ...replanned,
    status: 'queued',
    run_id: 'retry-run',
  })
  render(<CsvDestination env="sandbox" upload={upload} dirty={false} />)
  const user = userEvent.setup()
  await user.click(
    await screen.findByRole('button', { name: 'Prepare verified retry plan' }),
  )
  expect(screen.queryByText('Candidate loaded')).toBeNull()
  await user.click(
    screen.getByRole('button', { name: 'Confirm retry into candidate' }),
  )
  expect(loadUploadCandidate).toHaveBeenCalledWith({
    data: expect.objectContaining({
      confirmed: true,
      retry_of_run_id: 'failed-run',
      plan_hash: 'retry-plan',
    }),
  })
  await user.click(screen.getByText('Prior attempts (1)'))
  expect(screen.getByText('failed-run')).toBeTruthy()
})

function loadedReceipt(
  overrides: Partial<UploadCandidate> = {},
): UploadCandidate {
  return {
    ...prepared,
    status: 'loaded',
    loaded: true,
    run_id: 'verified-run',
    snapshot_id: '9223372036854775801',
    row_count: 3,
    base_ref: 'release_test',
    main_unchanged: true,
    plan_hash: 'verified-plan',
    job_snapshot_id: 'job-revision',
    ...overrides,
  }
}

it('opens a loaded candidate with the observed result first and hides preparation controls and technical columns', async () => {
  const receipt = loadedReceipt({
    attempts: [
      {
        run_id: 'failed-run',
        status: 'FAILURE',
        plan_hash: 'old-plan',
        job_snapshot_id: 'old-job',
        launch_ref_hash: 'old-base',
      },
    ],
  })
  vi.mocked(getUploadCandidate).mockResolvedValue(receipt)
  vi.mocked(previewUploadCandidate).mockResolvedValue({
    receipt,
    columns: [
      'upload_record_id_key',
      '_phlo_run_id',
      '_dlt_load_id',
      'upload_record_id',
      'result',
      'numeric_result',
    ],
    column_types: [
      'varchar',
      'varchar',
      'varchar',
      'varchar',
      'varchar',
      'double',
    ],
    rows: [
      {
        upload_record_id_key: 'generated-key',
        _phlo_run_id: 'verified-run',
        _dlt_load_id: 'load-key',
        upload_record_id: 'user-record',
        result: 'ND',
        numeric_result: null,
      },
    ],
    has_more: false,
    row_count: 3,
    snapshot_id: receipt.snapshot_id!,
    ref_hash: receipt.candidate_hash,
    query_id: 'query-id',
  })
  const reviewed = {
    ...upload,
    output_columns: [
      ...upload.output_columns!,
      {
        name: 'upload_record_id',
        type: 'string' as const,
        nullable: false,
        source_column: 'upload_record_id',
        derived: false,
      },
      {
        name: 'numeric_result',
        type: 'number' as const,
        nullable: true,
        source_column: 'result',
        derived: true,
      },
    ],
  }
  render(<CsvDestination env="sandbox" upload={reviewed} dirty={false} />)
  expect(
    await screen.findByRole('heading', { name: 'Candidate loaded' }),
  ).toBeTruthy()
  expect(screen.getByText('3 actual rows')).toBeTruthy()
  expect(screen.getByText('bronze.upload_aaaaaaaaaaaa')).toBeTruthy()
  expect(
    screen.getByText(/Base reference release_test is unchanged/),
  ).toBeTruthy()
  expect(screen.queryByRole('textbox', { name: 'New bronze table' })).toBeNull()
  expect(
    screen.queryByRole('button', { name: /Confirm|Estimate|Prepare/ }),
  ).toBeNull()
  const table = await screen.findByRole('table', {
    name: 'Candidate table preview',
  })
  expect(
    within(table)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent),
  ).toEqual(['upload_record_id', 'result', 'numeric_result'])
  expect(within(table).getByText('user-record')).toBeTruthy()
  expect(within(table).queryByText('generated-key')).toBeNull()
  const evidence = screen.getByText('Evidence').closest('details')!
  const attempts = screen.getByText('Prior attempts (1)').closest('details')!
  expect(evidence.open).toBe(false)
  expect(attempts.open).toBe(false)
  expect(
    screen.getByText('3 actual rows').compareDocumentPosition(evidence) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
  const user = userEvent.setup()
  await user.click(screen.getByText('Evidence'))
  expect(evidence.open).toBe(true)
  expect(within(evidence).getByText('9223372036854775801')).toBeTruthy()
  expect(within(evidence).getByText('verified-plan')).toBeTruthy()
  await user.click(screen.getByText('Prior attempts (1)'))
  expect(within(attempts).getByText('failed-run')).toBeTruthy()
  await user.click(
    screen.getByRole('checkbox', { name: 'Show technical columns (3)' }),
  )
  expect(
    within(table).getByRole('columnheader', { name: 'upload_record_id_key' }),
  ).toBeTruthy()
  expect(within(table).getByText('generated-key')).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'Refresh preview' }))
  expect(previewUploadCandidate).toHaveBeenCalledTimes(2)
  expect(loadUploadCandidate).not.toHaveBeenCalled()
  expect(planUploadCandidate).not.toHaveBeenCalled()
})

it.each(['queued', 'running'] as const)(
  'keeps %s work pending and never implies it loaded',
  async (status) => {
    vi.mocked(getUploadCandidate).mockResolvedValue({
      ...prepared,
      status,
      run_id: 'pending-run',
    })
    render(<CsvDestination env="sandbox" upload={upload} dirty={false} />)
    await screen.findByRole('button', { name: 'Check load status' })
    expect(screen.queryByText('Candidate loaded')).toBeNull()
    expect(screen.queryByText(/actual rows/)).toBeNull()
    expect(
      screen.queryByRole('button', { name: /Confirm|Prepare|Estimate/ }),
    ).toBeNull()
    expect(previewUploadCandidate).not.toHaveBeenCalled()
    expect(loadUploadCandidate).not.toHaveBeenCalled()
  },
)

it('ignores a late loaded preview after the user switches uploads', async () => {
  const receipt = loadedReceipt()
  vi.mocked(getUploadCandidate)
    .mockResolvedValueOnce(receipt)
    .mockRejectedValueOnce(new Error('not prepared'))
  let resolvePreview!: (
    value: Awaited<ReturnType<typeof previewUploadCandidate>>,
  ) => void
  vi.mocked(previewUploadCandidate).mockImplementation(
    () =>
      new Promise((resolve) => {
        resolvePreview = resolve
      }),
  )
  const { rerender } = render(
    <CsvDestination env="sandbox" upload={upload} dirty={false} />,
  )
  await waitFor(() => expect(previewUploadCandidate).toHaveBeenCalledOnce())
  rerender(
    <CsvDestination
      env="sandbox"
      upload={{ ...upload, upload_id: 'd'.repeat(32) }}
      dirty={false}
    />,
  )
  await screen.findByRole('button', { name: 'Prepare candidate destination' })
  resolvePreview({
    receipt,
    columns: ['result'],
    column_types: ['varchar'],
    rows: [{ result: 'OLD' }],
    has_more: false,
    row_count: 3,
    snapshot_id: receipt.snapshot_id!,
    ref_hash: 'written',
    query_id: null,
  })
  await waitFor(() => expect(screen.queryByText('OLD')).toBeNull())
  expect(screen.queryByText('Candidate loaded')).toBeNull()
})

it('immediately removes a previous upload preview when identity changes and uses review columns when output columns are empty', async () => {
  const receipt = loadedReceipt()
  vi.mocked(getUploadCandidate)
    .mockResolvedValueOnce(receipt)
    .mockRejectedValueOnce(new Error('not prepared'))
  vi.mocked(previewUploadCandidate).mockResolvedValue({
    receipt,
    columns: ['result', '_phlo_run_id'],
    column_types: ['varchar', 'varchar'],
    rows: [{ result: 'PRIVATE-PREVIOUS-UPLOAD', _phlo_run_id: 'verified-run' }],
    has_more: false,
    row_count: 3,
    snapshot_id: receipt.snapshot_id!,
    ref_hash: 'written',
    query_id: null,
  })
  const legacyReview: CsvUpload = {
    ...upload,
    output_columns: [],
    columns: [
      {
        column: 'Result',
        name: 'result',
        type: 'string',
        nullable: false,
        date_format: 'iso',
      },
    ],
  }
  const { rerender } = render(
    <CsvDestination env="sandbox" upload={legacyReview} dirty={false} />,
  )
  expect(await screen.findByText('PRIVATE-PREVIOUS-UPLOAD')).toBeTruthy()
  expect(
    screen.queryByRole('columnheader', { name: '_phlo_run_id' }),
  ).toBeNull()
  rerender(
    <CsvDestination
      env="sandbox"
      upload={{ ...legacyReview, upload_id: 'e'.repeat(32) }}
      dirty={false}
    />,
  )
  expect(screen.queryByText('PRIVATE-PREVIOUS-UPLOAD')).toBeNull()
  expect(screen.queryByRole('heading', { name: 'Candidate loaded' })).toBeNull()
  await screen.findByRole('button', { name: 'Prepare candidate destination' })
})
