// @vitest-environment jsdom
/** Exercises the real query route and its shared pending/error boundary. */
import * as React from 'react'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Route } from './_app/query'
import { Route as ShellRoute } from './_app'
import type { QuerySession } from '@/lib/data/api/query'
import type * as QueryApi from '@/lib/data/api/query'
import { PhloApiError } from '@/lib/data/api/client'
import { clearQueryWorkspaces } from '@/lib/query-workspace'

const submit = vi.hoisted(() => vi.fn())
const state = {
  loader: {
    catalog: {
      env: 'staging',
      nessie_ref: 'candidate',
      engine: 'trino',
      catalogs: [],
    },
    refs: [{ name: 'candidate' }],
    engines: [{ id: 'trino', status: 'configured' }],
    saved: [],
  },
}
vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: { component?: React.ComponentType }) => ({
    options,
    useLoaderData: () => state.loader,
    useSearch: () => ({ env: 'staging', sql: 'SELECT 7 AS answer' }),
  }),
  getRouteApi: () => ({
    useLoaderData: () => ({
      me: { principal_type: 'user', subject: 'route-test' },
    }),
  }),
  useRouter: () => ({ invalidate: () => Promise.resolve() }),
  Link: ({ to, children }: { to?: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}))
vi.mock('@/lib/data/api/query', async (importOriginal) => ({
  ...(await importOriginal<typeof QueryApi>()),
  submitQuery: submit,
}))
// CodeMirror's real DOM/input path is exercised by the built-browser smoke.
vi.mock('@/components/query/sql-editor', () => ({
  SqlEditor: ({
    value,
    onChange,
  }: {
    value: string
    onChange: (value: string) => void
  }) => (
    <textarea
      aria-label="SQL"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

beforeEach(() => {
  clearQueryWorkspaces()
  submit.mockReset()
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: () => undefined,
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function renderQuery() {
  const Page = Route.options.component
  if (!Page) throw new Error('Query component is missing')
  render(
    <main>
      <Page />
    </main>,
  )
}
function session(rows: Array<Record<string, number>>): QuerySession {
  return {
    id: 'query-7',
    env: 'staging',
    nessie_ref: 'candidate',
    engine: 'trino',
    evidence_available: true,
    status: 'completed',
    sql_hash: 'hash',
    created_at: '2026-10-10T10:00:00Z',
    updated_at: '2026-10-10T10:00:01Z',
    error: null,
    result: {
      columns: [{ name: 'answer', type: 'bigint' }],
      rows,
      has_more: false,
    },
  }
}

describe('query route states', () => {
  it('renders pending submission, a completed mutation result, then a permission error without stale results', async () => {
    let complete!: (value: QuerySession) => void
    submit.mockImplementationOnce(
      () =>
        new Promise<QuerySession>((resolve) => {
          complete = resolve
        }),
    )
    renderQuery()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Run' }))
    expect(
      screen
        .getByRole('button', { name: 'Submitting…' })
        .hasAttribute('disabled'),
    ).toBe(true)
    expect(screen.queryByRole('table', { name: 'Query results' })).toBeNull()
    await act(() => complete(session([{ answer: 7 }])))
    const result = await screen.findByRole('table', { name: 'Query results' })
    expect(within(result).getByRole('cell', { name: '7' })).toBeTruthy()
    submit.mockRejectedValueOnce(new PhloApiError(403))
    await user.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText('Query failed')).toBeTruthy()
    expect(
      screen.getAllByText(/Your account does not have permission/).length,
    ).toBeGreaterThan(0)
    expect(screen.queryByRole('table', { name: 'Query results' })).toBeNull()
  })

  it('distinguishes a successful empty result from a query failure', async () => {
    submit.mockResolvedValueOnce(session([]))
    renderQuery()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText('No rows returned')).toBeTruthy()
    expect(screen.getByText('The query completed successfully.')).toBeTruthy()
    expect(screen.queryByText('Query failed')).toBeNull()
  })

  it('exposes the registered loading and permission-error route boundaries', () => {
    const Pending = ShellRoute.options.pendingComponent
    const ErrorState = ShellRoute.options.errorComponent
    if (!Pending || !ErrorState)
      throw new Error('Shell state components are missing')
    const pending = render(<Pending />)
    expect(
      screen.getByRole('status', { name: 'Loading' }).getAttribute('aria-busy'),
    ).toBe('true')
    pending.unmount()
    render(<ErrorState error={new PhloApiError(403)} reset={() => undefined} />)
    expect(screen.getByRole('alert').textContent).toContain(
      'Your account does not have permission',
    )
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})
