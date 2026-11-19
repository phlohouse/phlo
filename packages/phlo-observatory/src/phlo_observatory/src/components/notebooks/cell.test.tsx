// @vitest-environment jsdom
/** Exercises notebook cells: Markdown rendering, outputs, staleness and the kernels-off state. */
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NotebookCellView } from './cell'
import { markdownBlocks } from './markdown'
import type { CellActions } from './cell'
import type { NotebookCell } from '@/lib/notebook'

afterEach(cleanup)

const actions = (): CellActions => ({
  change: vi.fn(),
  run: vi.fn(),
  move: vi.fn(),
  remove: vi.fn(),
  select: vi.fn(),
})
const trials: NotebookCell = {
  id: 'q',
  kind: 'sql',
  source: 'SELECT * FROM gold.trials',
  name: 'trials',
  engine: 'trino',
}
const share: NotebookCell = {
  id: 'd',
  kind: 'sql',
  source: 'SELECT count(*) AS n FROM trials',
  name: 'share',
  engine: 'duckdb',
}

describe('notebook cells', () => {
  it('renders Markdown as elements, never as HTML', () => {
    const blocks = markdownBlocks(
      '# Title\n\n<img src=x onerror=alert(1)>\n- a\n- b',
    )
    expect(blocks).toEqual([
      { kind: 'heading', level: 1, text: 'Title' },
      { kind: 'paragraph', text: '<img src=x onerror=alert(1)>' },
      { kind: 'list', items: ['a', 'b'] },
    ])
    render(
      <NotebookCellView
        cell={{
          id: 'm',
          kind: 'markdown',
          source: 'Uses `trials` **now**',
          name: null,
          engine: null,
        }}
        index={0}
        count={1}
        selected={false}
        canRun
        actions={actions()}
      />,
    )
    expect(screen.getByText('trials').tagName).toBe('CODE')
    expect(screen.getByText('now').tagName).toBe('STRONG')
  })

  it('shows what a Trino cell reads and its result table', () => {
    render(
      <NotebookCellView
        cell={trials}
        index={0}
        count={2}
        selected
        canRun
        state={{
          status: 'ok',
          seq: 1,
          at: Date.now(),
          capped: true,
          run: {
            status: 'ok',
            execution_count: 4,
            duration_ms: 12,
            outputs: [],
            truncated: false,
            table: {
              columns: [{ name: 'n', type: 'bigint' }],
              rows: [{ n: 7 }],
              row_count: 1,
            },
          },
        }}
        actions={actions()}
      />,
    )
    expect(screen.getByText('gold.trials')).toBeTruthy()
    expect(screen.getByText('[4]')).toBeTruthy()
    expect(screen.getByRole('table', { name: 'Query results' })).toBeTruthy()
    expect(
      screen.getByText(/first 100 rows the lakehouse returned/),
    ).toBeTruthy()
  })

  it('warns when an upstream result changed and reruns on request', async () => {
    const handlers = actions()
    render(
      <NotebookCellView
        cell={share}
        index={1}
        count={2}
        selected={false}
        canRun
        staleFrom={trials}
        state={{ status: 'ok', seq: 1, at: Date.now() }}
        actions={handlers}
      />,
    )
    expect(screen.getByText('Stale')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Run this cell' }))
    expect(handlers.run).toHaveBeenCalledOnce()
  })

  it('shows kernel errors and disables running when kernels are off', () => {
    render(
      <NotebookCellView
        cell={{
          id: 'p',
          kind: 'python',
          source: '1/0',
          name: null,
          engine: null,
        }}
        index={0}
        count={1}
        selected={false}
        canRun={false}
        state={{
          status: 'error',
          seq: 1,
          at: Date.now(),
          run: {
            status: 'error',
            execution_count: 2,
            duration_ms: 3,
            outputs: [
              {
                type: 'error',
                text: 'ZeroDivisionError: division by zero',
                name: null,
                png: null,
                ename: 'ZeroDivisionError',
              },
            ],
            truncated: false,
            table: null,
          },
        }}
        actions={actions()}
      />,
    )
    expect(screen.getByRole('alert').textContent).toContain('ZeroDivisionError')
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Run cell 1' })
        .disabled,
    ).toBe(true)
  })

  it('keeps an invalid result name out of the notebook', async () => {
    const handlers = actions()
    render(
      <NotebookCellView
        cell={trials}
        index={0}
        count={1}
        selected={false}
        canRun
        actions={handlers}
      />,
    )
    const field = screen.getByRole('textbox', {
      name: 'Result name for cell 1',
    })
    await userEvent.clear(field)
    await userEvent.type(field, '2bad{Enter}')
    expect(handlers.change).not.toHaveBeenCalled()
    await userEvent.clear(field)
    await userEvent.type(field, 'trial_rows{Enter}')
    expect(handlers.change).toHaveBeenCalledWith({ name: 'trial_rows' })
  })
})
