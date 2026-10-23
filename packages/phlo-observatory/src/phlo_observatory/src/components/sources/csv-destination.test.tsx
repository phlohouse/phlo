// @vitest-environment jsdom
/** Destination confirms a pinned candidate plan and waits for real readback before claiming loaded. */
import { cleanup, render, screen } from '@testing-library/react'
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
  it('keeps destination unavailable while column choices are unsaved', () => {
    vi.mocked(getUploadCandidate).mockRejectedValue(new Error('not prepared'))
    render(<CsvDestination env="sandbox" upload={upload} dirty />)
    expect(
      screen.getByRole<HTMLButtonElement>('button', {
        name: 'Prepare candidate destination',
      }).disabled,
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
      screen.getByRole('button', { name: 'Prepare candidate destination' }),
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
    await user.click(
      screen.getByRole('button', { name: 'Check run and preview candidate' }),
    )
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
  expect(screen.getByText(/Prior failed attempts: failed-run/)).toBeTruthy()
})
