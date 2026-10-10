// @vitest-environment jsdom
/** Verifies the assets table keeps header and cell semantics through sorting, paging and empty states. */
import * as React from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'
import { Route } from './_app/assets/index'
import type { ApiAsset } from '@/lib/data/api/assets'

type Search = {
  q: string
  filter?: 'all' | 'attention'
  layer?: 'bronze' | 'silver' | 'gold'
  cursor?: string
  cursorEnv?: 'prod' | 'staging'
  previousCursors: Array<string>
}

const state: {
  loader: { items: Array<ApiAsset>; env: 'prod'; next_cursor: string | null }
  search: Search
} = {
  loader: { items: [], env: 'prod', next_cursor: null },
  search: { q: '', previousCursors: [] },
}
const navigate = vi.fn((_options: unknown) => Promise.resolve())

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: { component?: React.ComponentType }) => ({
    options,
    useLoaderData: () => state.loader,
    useSearch: () => state.search,
    useNavigate: () => navigate,
  }),
  Link: ({ to, children }: { to?: string; children: React.ReactNode }) => (
    <a href={to ?? '/'}>{children}</a>
  ),
}))

afterEach(cleanup)
beforeEach(() => {
  navigate.mockClear()
  state.search = { q: '', previousCursors: [] }
})

function asset(id: string, rowCount: number | null): ApiAsset {
  return {
    id,
    key: id.split('.'),
    description: null,
    compute_kind: null,
    group_name: null,
    is_source: false,
    dependencies: [],
    last_materialization_at: null,
    last_run_id: null,
    relation: null,
    history_scoped: true,
    row_count: rowCount,
    size_bytes: null,
  }
}

function renderPage() {
  const AssetsPage = Route.options.component
  if (!AssetsPage) throw new Error('AssetsPage is not registered on the route.')
  return render(
    <main>
      <AssetsPage />
    </main>,
  )
}

/** Each body row of the table, as its cells' text. */
function bodyRows(table: HTMLElement) {
  const [header, ...rows] = within(table).getAllByRole('row')
  expect(within(header).getAllByRole('columnheader')).toHaveLength(8)
  return rows.map((row) =>
    within(row)
      .getAllByRole('cell')
      .map((cell) => cell.textContent),
  )
}

describe('the assets table', () => {
  it('keeps headers and cells while sorting', async () => {
    state.loader = {
      items: [asset('silver.b', 20), asset('silver.a', 5), asset('gold.c', 9)],
      env: 'prod',
      next_cursor: null,
    }
    const user = userEvent.setup()
    renderPage()
    const table = screen.getByRole('table', { name: 'Assets' })
    const headers = within(table).getAllByRole('columnheader')
    expect(headers.map((header) => header.getAttribute('aria-sort'))).toEqual([
      'ascending',
      'none',
      'none',
      'none',
      'none',
      'none',
      'none',
      'none',
    ])
    expect(bodyRows(table).map((cells) => cells.length)).toEqual([8, 8, 8])
    expect(bodyRows(table).map((cells) => cells[0])).toEqual([
      expect.stringContaining('gold.c'),
      expect.stringContaining('silver.a'),
      expect.stringContaining('silver.b'),
    ])
    expect(await axe(document.body)).toHaveProperty('violations', [])

    await user.click(within(headers[4]).getByRole('button'))
    expect(headers[0].getAttribute('aria-sort')).toBe('none')
    const direction = headers[4].getAttribute('aria-sort')
    const byRows = ['silver.a', 'gold.c', 'silver.b']
    const sorted = bodyRows(table)
    expect(sorted.map((cells) => cells.length)).toEqual([8, 8, 8])
    expect(sorted.map((cells) => cells[0])).toEqual(
      (direction === 'ascending' ? byRows : byRows.reverse()).map((id) =>
        expect.stringContaining(id),
      ),
    )
    expect(direction).toMatch(/^(ascending|descending)$/)
    expect(await axe(document.body)).toHaveProperty('violations', [])
  })

  it('keeps headers and cells on a later page and pages by keyboard', async () => {
    state.loader = {
      items: [asset('silver.page2', 1)],
      env: 'prod',
      next_cursor: 'c3',
    }
    state.search = {
      q: '',
      cursor: 'c2',
      cursorEnv: 'prod',
      previousCursors: ['c1'],
    }
    const user = userEvent.setup()
    renderPage()
    const table = screen.getByRole('table', { name: 'Assets' })
    expect(bodyRows(table)).toHaveLength(1)
    expect(bodyRows(table)[0]).toHaveLength(8)
    expect(screen.getAllByText(/on page 3/).length).toBeGreaterThan(0)

    const next = screen.getAllByRole('button', { name: 'Next' })[1]
    next.focus()
    await user.keyboard('{Enter}')
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(await axe(document.body)).toHaveProperty('violations', [])
  })

  it('keeps one full-width cell row when nothing matches', async () => {
    state.loader = { items: [], env: 'prod', next_cursor: null }
    renderPage()
    const table = screen.getByRole('table', { name: 'Assets' })
    const rows = within(table).getAllByRole('row')
    expect(within(rows[0]).getAllByRole('columnheader')).toHaveLength(8)
    expect(rows).toHaveLength(2)
    const [cell] = within(rows[1]).getAllByRole('cell')
    expect(cell.getAttribute('aria-colspan')).toBe('8')
    expect(cell.textContent).toContain('No assets match')
    expect(await axe(document.body)).toHaveProperty('violations', [])
  })
})
