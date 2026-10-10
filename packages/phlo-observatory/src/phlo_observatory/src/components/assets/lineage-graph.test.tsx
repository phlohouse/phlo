// @vitest-environment jsdom
/** Verifies the lineage graph's keyboard-reachable text alternative matches the drawn graph. */
import * as React from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'
import { LineageGraph, lineageRows } from './lineage-graph'
import type { LineageColumn } from '@/lib/data/types'

vi.mock('@tanstack/react-router', () => ({
  // The canvas is client-only; tests see the server fallback, as a first paint does.
  ClientOnly: ({ fallback }: { fallback: React.ReactNode }) => <>{fallback}</>,
  Link: ({
    to,
    children,
    className,
  }: {
    to?: string
    children: React.ReactNode
    className?: string
  }) => (
    <a href={to ?? '/'} className={className}>
      {children}
    </a>
  ),
}))

afterEach(cleanup)

const columns: Array<LineageColumn> = [
  {
    heading: 'Declared upstream',
    nodes: [
      {
        id: 'bronze.orders',
        name: 'bronze.orders',
        sub: 'Freshness not observed',
        tone: 'source',
        href: '/assets/bronze.orders',
      },
      {
        id: 'bronze.customers',
        name: 'bronze.customers',
        sub: 'Freshness not observed',
        tone: 'source',
        href: '/assets/bronze.customers',
      },
    ],
  },
  {
    heading: 'This asset',
    nodes: [
      {
        id: 'silver.orders',
        name: 'silver.orders',
        sub: 'iceberg.silver.orders',
        tone: 'self',
      },
    ],
  },
  {
    heading: 'Downstream · 1',
    nodes: [
      {
        id: 'gold.revenue',
        name: 'gold.revenue',
        sub: 'iceberg.gold.revenue',
        tone: 'job',
        href: '/assets/gold.revenue',
      },
    ],
  },
]
const edges: Array<[string, string]> = [
  ['bronze.orders', 'silver.orders'],
  ['bronze.customers', 'silver.orders'],
  ['silver.orders', 'gold.revenue'],
]

function renderGraph(props: React.ComponentProps<typeof LineageGraph>) {
  return render(
    <main>
      <LineageGraph {...props} />
    </main>,
  )
}

async function openTable() {
  await userEvent.click(
    screen.getByRole('button', { name: /^Lineage as a table/ }),
  )
  return screen.getByRole('table')
}

function rowFor(table: HTMLElement, name: string) {
  const header = within(table).getByRole('rowheader', {
    name: new RegExp(`^${name.replace('.', '\\.')}`),
  })
  return header.closest('tr')!
}

describe('LineageGraph text alternative', () => {
  it('lists the same nodes and directed edges as the graph, marking the current asset', async () => {
    renderGraph({
      columns,
      edges,
      env: 'prod',
      label:
        '2 declared upstream assets feed silver.orders; 1 downstream assets depend on it in prod.',
    })
    expect(
      screen.getByRole('group', { name: /feed silver\.orders/ }),
    ).toBeTruthy()
    const table = await openTable()
    expect(table.querySelector('caption')!.textContent).toMatch(
      /^Lineage of silver\.orders/,
    )
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((h) => h.textContent),
    ).toEqual(['Asset', 'Position', 'Fed by (upstream)', 'Feeds (downstream)'])
    // One row per drawn node, in column order.
    expect(
      within(table)
        .getAllByRole('rowheader')
        .map((h) => h.querySelector('span')!.textContent),
    ).toEqual([
      'bronze.orders',
      'bronze.customers',
      'silver.orders (this asset)',
      'gold.revenue',
    ])

    const self = rowFor(table, 'silver.orders')
    expect(self.getAttribute('aria-current')).toBe('true')
    const selfCells = within(self).getAllByRole('cell')
    expect(selfCells[0].textContent).toBe('This asset')
    expect(
      within(selfCells[1])
        .getAllByRole('link')
        .map((a) => a.textContent),
    ).toEqual(['bronze.orders', 'bronze.customers'])
    expect(
      within(selfCells[2])
        .getAllByRole('link')
        .map((a) => a.getAttribute('href')),
    ).toEqual(['/assets/gold.revenue'])

    // Edge direction is kept from the other end too.
    const orders = within(rowFor(table, 'bronze.orders')).getAllByRole('cell')
    expect(orders[0].textContent).toBe('Declared upstream')
    expect(orders[1].textContent).toBe('None')
    expect(orders[2].textContent).toBe('silver.orders (this asset)')

    const results = await axe(document.body)
    expect(results.violations).toHaveLength(0)
  })

  it('lets a keyboard user open the table and follow an upstream relationship', async () => {
    const user = userEvent.setup()
    renderGraph({ columns, edges, env: 'prod', label: 'Lineage.' })
    const toggle = screen.getByRole('button', { name: /^Lineage as a table/ })
    expect(toggle.textContent).toContain('4 assets, 3 relationships')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')

    await user.tab()
    expect(document.activeElement).toBe(toggle)
    await user.keyboard('{Enter}')
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('table')).toBeTruthy()

    await user.tab()
    expect(document.activeElement?.textContent).toBe('bronze.orders')
    expect(document.activeElement?.getAttribute('href')).toBe(
      '/assets/bronze.orders',
    )
    const results = await axe(document.body)
    expect(results.violations).toHaveLength(0)
  })

  it('states when the asset has no declared lineage', async () => {
    renderGraph({
      columns: [{ ...columns[0], nodes: [] }, columns[1]],
      edges: [],
      env: 'prod',
      label: '0 declared upstream assets feed silver.orders.',
    })
    expect(screen.getByRole('button').textContent).toContain(
      '1 asset, 0 relationships',
    )
    const table = await openTable()
    expect(
      screen.getByText(
        'No declared upstream or downstream assets for silver.orders.',
      ),
    ).toBeTruthy()
    const cells = within(rowFor(table, 'silver.orders')).getAllByRole('cell')
    expect(cells.map((c) => c.textContent)).toEqual([
      'This asset',
      'None',
      'None',
    ])
    const results = await axe(document.body)
    expect(results.violations).toHaveLength(0)
  })
})

describe('lineageRows', () => {
  it('keeps only edges the graph draws, once each', () => {
    const { rows, edges: drawn } = lineageRows(columns, [
      ...edges,
      ['silver.orders', 'gold.revenue'],
      ['bronze.unknown', 'silver.orders'],
    ])
    expect(drawn).toEqual(edges)
    expect(
      rows
        .find((r) => r.node.id === 'silver.orders')!
        .upstream.map((n) => n.id),
    ).toEqual(['bronze.orders', 'bronze.customers'])
  })
})
