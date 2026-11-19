/** Covers notebook cell naming, staleness, outline and the tables Trino cells read. */
import { describe, expect, it } from 'vitest'
import {
  freeName,
  mentions,
  newCell,
  notebookFileName,
  notebookTree,
  outline,
  sqlReads,
  staleCells,
} from './notebook'
import type { CellState, NotebookCell } from './notebook'

const cell = (
  id: string,
  kind: NotebookCell['kind'],
  source: string,
  name: string | null = null,
  engine: NotebookCell['engine'] = kind === 'sql' ? 'trino' : null,
): NotebookCell => ({ id, kind, source, name, engine })
const ran = (seq: number, status: CellState['status'] = 'ok'): CellState => ({
  status,
  seq,
  at: 0,
})

describe('notebook cells', () => {
  it('gives new SQL cells a free result name', () => {
    const cells = [cell('a', 'sql', 'SELECT 1', 'query_1')]
    expect(freeName(cells, 'query')).toBe('query_2')
    const added = newCell(cells, 'sql')
    expect(added).toMatchObject({
      kind: 'sql',
      name: 'query_2',
      engine: 'trino',
    })
    expect(newCell(cells, 'python').name).toBeNull()
  })

  it('matches names as whole identifiers only', () => {
    expect(mentions('SELECT * FROM trials', 'trials')).toBe(true)
    expect(mentions('trials_2 = 1', 'trials')).toBe(false)
    expect(mentions('gold.trials', 'trials')).toBe(false)
    expect(mentions('x = trials.starts', 'trials')).toBe(true)
  })

  it('marks a cell stale when a result it reads ran again later', () => {
    const cells = [
      cell('q', 'sql', 'SELECT 1', 'trials'),
      cell('py', 'python', 'growth = trials.head()', 'growth'),
      cell('dd', 'sql', 'SELECT * FROM growth', 'share', 'duckdb'),
      cell('other', 'python', 'x = 1'),
    ]
    expect(
      staleCells(cells, { q: ran(1), py: ran(2), dd: ran(3), other: ran(4) })
        .size,
    ).toBe(0)
    const stale = staleCells(cells, {
      q: ran(5),
      py: ran(2),
      dd: ran(3),
      other: ran(4),
    })
    expect([...stale]).toEqual([['py', 'q']])
    // A failed rerun does not make readers stale.
    expect(staleCells(cells, { q: ran(5, 'error'), py: ran(2) }).size).toBe(0)
  })

  it('lists lakehouse tables from FROM and JOIN, ignoring comments', () => {
    expect(
      sqlReads(
        '-- FROM old.table\nSELECT * FROM gold.cgt_trial_starts t JOIN lake.silver."sites" s ON true',
      ),
    ).toEqual(['gold.cgt_trial_starts', 'lake.silver.sites'])
    expect(sqlReads('SELECT * FROM trials')).toEqual([])
  })

  it('builds an outline from headings and named cells', () => {
    expect(
      outline([
        cell('m', 'markdown', '# Title\n\ntext\n## Part'),
        cell('q', 'sql', 'SELECT 1', 'trials'),
        cell('p', 'python', 'x = 1'),
      ]),
    ).toEqual([
      { kind: 'heading', id: 'm', text: 'Title', level: 1 },
      { kind: 'heading', id: 'm', text: 'Part', level: 2 },
      { kind: 'cell', id: 'q', text: 'trials' },
    ])
  })

  it('turns typed names into safe notebook paths', () => {
    expect(notebookFileName('market/New analysis')).toBe(
      'market/New-analysis.ipynb',
    )
    expect(notebookFileName('scratch.ipynb')).toBe('scratch.ipynb')
    expect(notebookFileName('../etc')).toBeNull()
    expect(notebookFileName('  ')).toBeNull()
  })

  it('groups paths into folders before files', () => {
    const tree = notebookTree([
      'b.ipynb',
      'market/a.ipynb',
      'market/qc/c.ipynb',
    ])
    expect(tree.map((node) => node.name)).toEqual(['market', 'b.ipynb'])
    expect(tree[0].children.map((node) => node.path)).toEqual([
      'market/qc',
      'market/a.ipynb',
    ])
  })
})
