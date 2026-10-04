/** Verifies WAP observations distinguish run status from verified publication evidence. */
import * as React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { WapObservations } from './wap-runs'
import { wapPageSchema } from '@/lib/data/api/branches'

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    params,
    search,
  }: {
    children: React.ReactNode
    params: { jobName: string }
    search: { env: string }
  }) => (
    <a href={`/pipelines/${params.jobName}?env=${search.env}`}>{children}</a>
  ),
}))

const page = wapPageSchema.parse({
  env: 'prod',
  items: [
    {
      logical_run_id: 'observed-run',
      run_id: 'dagster-run',
      job_id: 'daily',
      status: 'SUCCESS',
      created_at: '2026-10-03T09:11:12Z',
      staging_ref: 'pipeline-run-observed-run',
      catalog_system: 'nessie',
      strategy: 'branch',
      branch_state: 'present',
      branch_hash: '1234567890abcdef',
      report_state: 'missing',
      lifecycle_status: null,
      reported_at: null,
    },
  ],
  scanned_runs: 48,
  scan_limit: 500,
  scan_limited: false,
  catalog_available: true,
})

describe('WAP observations', () => {
  it('keeps run success separate from publication and offers no mutations', () => {
    const html = renderToStaticMarkup(<WapObservations data={page} />)
    expect(html).toContain('pipeline-run-observed-run')
    expect(html).toContain('Present in Nessie')
    expect(html).toContain('Report not available')
    expect(html).toContain('success does not establish that data was published')
    expect(html).toContain('/pipelines/daily?env=prod')
    expect(html).toContain('3 Oct 2026, 09:11:12 UTC')
    expect(html).not.toContain('<button')
    expect(html).not.toContain('promoted')
  })

  it('distinguishes verified snapshot namespaces from unverifiable reports', () => {
    const run = page.items[0]
    const data = wapPageSchema.parse({
      ...page,
      items: [
        {
          ...run,
          strategy: 'snapshot',
          branch_state: 'not_applicable',
          branch_hash: null,
          report_state: 'verified',
          lifecycle_status: 'candidates_staged',
          reported_at: '2026-10-03T10:11:12Z',
        },
        {
          ...run,
          run_id: 'other-run',
          report_state: 'invalid',
          lifecycle_status: null,
        },
      ],
    })
    const html = renderToStaticMarkup(<WapObservations data={data} />)
    expect(html).toContain('Candidate namespace · no Nessie branch')
    expect(html).toContain('candidates staged')
    expect(html).toContain('Report could not be verified')
    expect(html).toContain('2026-10-03T10:11:12Z')
  })

  it('does not describe an incomplete inventory as no existing branches', () => {
    const html = renderToStaticMarkup(
      <WapObservations
        data={{
          ...page,
          items: [],
          scanned_runs: 500,
          scan_limited: true,
          catalog_available: false,
        }}
      />,
    )
    expect(html).toContain('500 most recent Dagster runs')
    expect(html).toContain('complete Nessie reference list is unavailable')
    expect(html).toContain(
      'This does not prove that no temporary branches exist',
    )
    expect(html).toContain('No WAP runs observed')
  })
})
