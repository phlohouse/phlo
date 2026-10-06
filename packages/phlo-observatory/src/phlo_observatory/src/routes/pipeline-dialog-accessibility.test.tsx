// @vitest-environment jsdom
import * as React from 'react'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'
import { Route } from './_app/pipelines/$jobName'

const pipelineData = {
  job: {
    id: 'daily_ingest',
    repository_name: 'warehouse',
    description: null,
    domain: 'analytics',
    owners: [],
    source: null,
    feeds_batch_release: false,
    selected_assets: [],
  },
  runs: [],
  schedules: [],
  summary: {
    scanned_runs: 0,
    counts_by_status: {},
    duration_histogram_seconds: {},
  },
  patterns: { scanned_runs: 0, items: [] },
  siblings: [],
  maintenance: { env: 'prod', status: 'configured', items: [] },
  selected: null,
  events: null,
  env: 'prod',
}

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: { component?: React.ComponentType }) => ({
    options,
    useLoaderData: () => pipelineData,
    useSearch: () => ({ env: 'prod' }),
    useNavigate: () => () => Promise.resolve(),
  }),
  getRouteApi: () => ({
    useLoaderData: () => ({
      me: {
        subject: 'pipeline-test',
        principal_type: 'user',
        email: null,
        roles: [],
        permissions: {},
      },
    }),
  }),
  Link: ({ to, children }: { to?: string; children: React.ReactNode }) => (
    <a href={to ?? '/'}>{children}</a>
  ),
  useRouter: () => ({ invalidate: () => Promise.resolve() }),
}))

afterEach(cleanup)

describe('the current pipeline launch dialog', () => {
  it('names its controls and keeps keyboard focus in the opened dialog', async () => {
    const user = userEvent.setup()
    const PipelinePage = Route.options.component
    if (!PipelinePage)
      throw new Error('PipelinePage is not registered on the route.')
    render(<PipelinePage />)

    const opener = screen.getByRole('button', { name: 'Launch run' })
    await user.click(opener)
    const dialog = await screen.findByRole('dialog', { name: 'Launch run' })
    expect(screen.getByRole('textbox', { name: /^Partition key/ })).toBeTruthy()
    await waitFor(() =>
      expect(dialog.contains(document.activeElement)).toBe(true),
    )
    const partitionInput = screen.getByRole('textbox', {
      name: /^Partition key/,
    })
    await user.tab()
    expect(dialog.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).not.toBe(partitionInput)
    await user.tab({ shift: true })
    expect(document.activeElement).toBe(partitionInput)

    const results = await axe(document.body)
    expect(results.violations).toHaveLength(0)

    fireEvent.keyDown(dialog, { key: 'Escape', code: 'Escape' })
    await waitFor(() =>
      expect(document.querySelector('[role="dialog"]')).toBeNull(),
    )
    expect(document.activeElement).toBe(opener)
  })
})
