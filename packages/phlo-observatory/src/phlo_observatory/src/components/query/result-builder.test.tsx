// @vitest-environment jsdom
/** Exercises the builder controls against real result rows. */
import * as React from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResultsChart, ResultsPivot } from './result-builder'
import type { QueryResult } from '@/lib/data/api/query'
import { restoreQueryView, savedQueryMetadata } from '@/lib/data/api/query-view'

vi.mock('@/components/ui/chart', () => ({
  ChartContainer: ({
    children,
    label,
  }: {
    children: React.ReactNode
    label: string
  }) => (
    <div role="img" aria-label={label}>
      {children}
    </div>
  ),
  ChartTooltip: () => null,
  ChartTooltipContent: () => null,
}))

afterEach(cleanup)
const result: QueryResult = {
  columns: ['week', 'product', 'potency'].map((name) => ({ name, type: null })),
  rows: [
    { week: 'week one', product: 'CGT-02', potency: 90 },
    { week: 'week one', product: 'CGT-02', potency: 110 },
    { week: 'week one', product: 'CGT-04', potency: 80 },
  ],
  has_more: true,
}

describe('result builders', () => {
  it('recomputes the pivot when the user selects a grouping column', async () => {
    const user = userEvent.setup()
    render(<ResultsPivot result={result} />)
    const table = screen.getByRole('table', { name: 'Pivot results' })
    expect(within(table).getByText('93.3333')).toBeTruthy()
    await user.click(screen.getByRole('combobox', { name: 'Pivot columns' }))
    await user.click(await screen.findByRole('option', { name: 'product' }))
    expect(
      within(table).getByRole('columnheader', { name: 'CGT-02' }),
    ).toBeTruthy()
    expect(within(table).getByRole('cell', { name: '100' })).toBeTruthy()
    expect(within(table).getByRole('cell', { name: '80' })).toBeTruthy()
    expect(screen.getByText(/Results are truncated/)).toBeTruthy()
  })

  it('exposes reference limits only when requested and rejects reversed limits', async () => {
    const user = userEvent.setup()
    render(<ResultsChart result={result} />)
    expect(
      screen.queryByRole('spinbutton', { name: 'Reference lower limit' }),
    ).toBeNull()
    await user.click(screen.getByRole('checkbox', { name: 'Reference band' }))
    const lower = screen.getByRole('spinbutton', {
      name: 'Reference lower limit',
    })
    await user.clear(lower)
    await user.type(lower, '130')
    expect(screen.getByRole('alert').textContent).toContain('lower limit below')
    expect(screen.getByText(/do not change audit thresholds/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /^Bar$/ }))
    expect(
      screen
        .getByRole('button', { name: /^Bar$/ })
        .getAttribute('aria-pressed'),
    ).toBe('true')
  })
  it('restores a saved grouping and aggregation when the query is reopened', async () => {
    const user = userEvent.setup()
    let config = restoreQueryView()
    const first = render(
      <ResultsPivot
        result={result}
        onConfigChange={(next) => {
          config = next
        }}
      />,
    )
    await user.click(screen.getByRole('combobox', { name: 'Pivot columns' }))
    await user.click(await screen.findByRole('option', { name: 'product' }))
    const metadata = savedQueryMetadata(config)
    first.unmount()
    render(<ResultsPivot result={result} config={restoreQueryView(metadata)} />)
    const table = screen.getByRole('table', { name: 'Pivot results' })
    expect(
      within(table).getByRole('columnheader', { name: 'CGT-02' }),
    ).toBeTruthy()
    expect(within(table).getByRole('cell', { name: '100' })).toBeTruthy()
  })
  it('shows an unavailable state instead of plotting categorical values as scatter coordinates', async () => {
    const user = userEvent.setup()
    render(<ResultsChart result={result} />)
    await user.click(screen.getByRole('button', { name: 'Scatter' }))
    expect(screen.getByRole('status').textContent).toContain(
      'Scatter requires a numeric X axis',
    )
    expect(screen.queryByRole('img')).toBeNull()
  })
})
