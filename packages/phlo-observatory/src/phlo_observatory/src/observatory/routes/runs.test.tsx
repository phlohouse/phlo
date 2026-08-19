// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ObservatoryRun } from '@/observatory/api/types'
import { RunReportLink, runReportIdentity } from '@/routes/runs'

vi.mock('@tanstack/react-router', () => ({
  Link: (props: {
    to: string
    params: Record<string, string>
    children: React.ReactNode
  }) => (
    <a
      data-testid="run-report-link"
      data-to={props.to}
      data-project-id={props.params.projectId}
      data-run-id={props.params.runId}
      data-attempt={props.params.attempt}
    >
      {props.children}
    </a>
  ),
  createFileRoute: () => () => ({}) as unknown as never,
}))

function baseRun(overrides: Partial<ObservatoryRun> = {}): ObservatoryRun {
  return {
    id: 'finance/daily-orders',
    name: 'Daily orders refresh',
    status: 'succeeded',
    started_at: null,
    completed_at: null,
    duration_seconds: null,
    assets: [],
    checks: [],
    logs: [],
    metadata: {},
    ...overrides,
  }
}

describe('Runs report identity link', () => {
  afterEach(cleanup)

  it('renders a TanStack Router link to the exact report route for canonical identity', () => {
    render(
      <RunReportLink
        run={baseRun({
          report_identity: {
            project_id: 'finance',
            run_id: 'daily-orders',
            attempt: 2,
          },
        })}
      />,
    )

    const link = screen.getByTestId('run-report-link')
    expect(link).toBeTruthy()
    expect(link.getAttribute('data-to')).toBe(
      '/runs/$projectId/$runId/attempts/$attempt/report',
    )
    expect(link.getAttribute('data-project-id')).toBe('finance')
    expect(link.getAttribute('data-run-id')).toBe('daily-orders')
    expect(link.getAttribute('data-attempt')).toBe('2')
    expect(screen.getByText('Open run report')).toBeTruthy()
    expect(screen.getByText('finance/daily-orders · attempt 2')).toBeTruthy()
  })

  it.each([
    ['missing report_identity', baseRun()],
    ['null report_identity', baseRun({ report_identity: null })],
    [
      'empty project_id',
      baseRun({
        report_identity: { project_id: '', run_id: 'daily-orders', attempt: 2 },
      }),
    ],
    [
      'empty run_id',
      baseRun({
        report_identity: { project_id: 'finance', run_id: '  ', attempt: 2 },
      }),
    ],
    [
      'zero attempt',
      baseRun({
        report_identity: {
          project_id: 'finance',
          run_id: 'daily-orders',
          attempt: 0,
        },
      }),
    ],
    [
      'non-integer attempt',
      baseRun({
        report_identity: {
          project_id: 'finance',
          run_id: 'daily-orders',
          attempt: 1.5,
        },
      }),
    ],
    [
      'legacy manifest run',
      baseRun({
        id: 'keystone-run-0041',
        metadata: { evidence_source: 'lakehouse_manifest' },
      }),
    ],
    [
      'recovered operation run',
      baseRun({
        id: 'op-123',
        metadata: { operation_id: 'op-123', recovered_from: 'operation' },
      }),
    ],
  ])('renders no link for %s', (_label, run) => {
    const { container } = render(<RunReportLink run={run} />)
    expect(
      container.querySelector('[data-testid="run-report-link"]'),
    ).toBeNull()
  })

  it('runReportIdentity returns the identity for a complete canonical run', () => {
    expect(
      runReportIdentity(
        baseRun({
          report_identity: {
            project_id: 'finance',
            run_id: 'daily-orders',
            attempt: 2,
          },
        }),
      ),
    ).toEqual({ project_id: 'finance', run_id: 'daily-orders', attempt: 2 })
  })

  it('runReportIdentity returns null when identity is incomplete', () => {
    expect(runReportIdentity(baseRun())).toBeNull()
    expect(
      runReportIdentity(
        baseRun({
          report_identity: { project_id: 'finance', run_id: '', attempt: 2 },
        }),
      ),
    ).toBeNull()
  })
})
