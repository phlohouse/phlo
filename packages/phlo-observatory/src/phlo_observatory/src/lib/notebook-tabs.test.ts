// @vitest-environment jsdom
/** Covers open notebook tabs and the unsaved work each one keeps. */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  dirtyPaths,
  dropDraft,
  keepDraft,
  readDraft,
  readTabs,
  renameTab,
  withTab,
  writeTabs,
} from './notebook-tabs'
import type { NotebookCell } from './notebook'

const cell: NotebookCell = {
  id: 'c1',
  kind: 'python',
  source: 'x = 1',
  name: null,
  engine: null,
}

describe('notebook tabs', () => {
  beforeEach(() => {
    globalThis.localStorage.clear()
    dropDraft('prod', 'a.ipynb')
    dropDraft('prod', 'b.ipynb')
  })

  it('keeps tabs per environment and follows a rename', () => {
    writeTabs('prod', withTab(withTab([], 'a.ipynb'), 'b.ipynb'))
    expect(readTabs('prod')).toEqual(['a.ipynb', 'b.ipynb'])
    expect(readTabs('staging')).toEqual([])
    expect(withTab(readTabs('prod'), 'a.ipynb')).toEqual(['a.ipynb', 'b.ipynb'])
    renameTab('prod', 'a.ipynb', 'c.ipynb')
    expect(readTabs('prod')).toEqual(['c.ipynb', 'b.ipynb'])
  })

  it('reports only drafts that differ from what was saved', () => {
    const saved = { cells: [cell], packages: [], digest: 'd' }
    keepDraft('prod', 'a.ipynb', {
      cells: [cell],
      packages: [],
      saved,
      states: {},
    })
    expect(dirtyPaths('prod')).toEqual([])
    keepDraft('prod', 'b.ipynb', {
      cells: [{ ...cell, source: 'x = 2' }],
      packages: [],
      saved,
      states: {},
    })
    expect(dirtyPaths('prod')).toEqual(['b.ipynb'])
    expect(readDraft('prod', 'b.ipynb')?.cells[0].source).toBe('x = 2')
    dropDraft('prod', 'b.ipynb')
    expect(dirtyPaths('prod')).toEqual([])
  })
})
