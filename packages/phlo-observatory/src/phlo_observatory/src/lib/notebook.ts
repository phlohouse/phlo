/** Pure notebook logic: cell factories, staleness, outline and the tables SQL cells read. */
import type { CellRun, NotebookCellData } from '@/lib/data/api/notebooks'

export type NotebookCell = NotebookCellData
export type CellKind = NotebookCell['kind']

/** What one run of a cell produced in this browser session. */
export type CellState = {
  status: 'running' | 'ok' | 'error' | 'timeout'
  /** Order of this run among all runs in the session; drives staleness. */
  seq: number
  at: number
  run?: CellRun
  /** The lakehouse had more rows than the preview limit returned. */
  capped?: boolean
  message?: string
}

export const RESULT_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/

export function newCellId() {
  const bytes = new Uint8Array(4)
  globalThis.crypto.getRandomValues(bytes)
  return `c${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`
}

/** A name nothing else in the notebook uses yet, such as `query_2`. */
export function freeName(cells: ReadonlyArray<NotebookCell>, stem: string) {
  const taken = new Set(cells.map((cell) => cell.name).filter(Boolean))
  for (let n = 1; ; n++) if (!taken.has(`${stem}_${n}`)) return `${stem}_${n}`
}

export function newCell(
  cells: ReadonlyArray<NotebookCell>,
  kind: CellKind,
): NotebookCell {
  if (kind === 'sql')
    return {
      id: newCellId(),
      kind,
      source: 'SELECT 1',
      name: freeName(cells, 'query'),
      engine: 'trino',
    }
  if (kind === 'python')
    return { id: newCellId(), kind, source: '', name: null, engine: null }
  return { id: newCellId(), kind, source: '', name: null, engine: null }
}

export function starterCells(): Array<NotebookCell> {
  return [
    {
      id: newCellId(),
      kind: 'markdown',
      source: '# New notebook\n\nWhat question does this notebook answer?',
      name: null,
      engine: null,
    },
    {
      id: newCellId(),
      kind: 'sql',
      source: 'SELECT 1 AS answer',
      name: 'query_1',
      engine: 'trino',
    },
  ]
}

export function cellLabel(cell: NotebookCell) {
  if (cell.kind === 'python') return 'Python'
  if (cell.kind === 'sql')
    return cell.engine === 'duckdb' ? 'SQL · DuckDB' : 'SQL · Trino'
  return 'Text'
}

/** Whether `source` mentions `name` as a whole identifier. */
export function mentions(source: string, name: string) {
  return new RegExp(`(^|[^A-Za-z0-9_.])${name}(?![A-Za-z0-9_])`).test(source)
}

/**
 * A cell is stale when a result it reads was made again after the cell last ran.
 * Returns the id of the newer upstream cell, keyed by the stale cell's id.
 */
export function staleCells(
  cells: ReadonlyArray<NotebookCell>,
  states: Readonly<Record<string, CellState | undefined>>,
): Map<string, string> {
  const stale = new Map<string, string>()
  for (const cell of cells) {
    const mine = states[cell.id]
    if (cell.kind === 'markdown' || !mine || mine.status === 'running') continue
    if (cell.kind === 'sql' && cell.engine !== 'duckdb') continue
    for (const other of cells) {
      if (other.id === cell.id || !other.name) continue
      const theirs = states[other.id]
      if (
        theirs &&
        theirs.status === 'ok' &&
        theirs.seq > mine.seq &&
        mentions(cell.source, other.name)
      ) {
        stale.set(cell.id, other.id)
        break
      }
    }
  }
  return stale
}

/** Lakehouse tables a Trino cell reads, from its FROM and JOIN clauses. */
export function sqlReads(source: string): Array<string> {
  const text = source
    .replace(/--[^\n]*/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
  const found = new Set<string>()
  const pattern =
    /\b(?:from|join)\s+((?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)(?:\.(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)){1,2})/gi
  for (const match of text.matchAll(pattern))
    found.add(match[1].replace(/"/g, ''))
  return [...found]
}

export type OutlineItem =
  | { kind: 'heading'; id: string; text: string; level: number }
  | { kind: 'cell'; id: string; text: string }

export function outline(
  cells: ReadonlyArray<NotebookCell>,
): Array<OutlineItem> {
  const items: Array<OutlineItem> = []
  for (const cell of cells) {
    if (cell.kind === 'markdown') {
      for (const line of cell.source.split('\n')) {
        const heading = /^(#{1,3})\s+(.+)$/.exec(line.trim())
        if (heading)
          items.push({
            kind: 'heading',
            id: cell.id,
            text: heading[2],
            level: heading[1].length,
          })
      }
    } else if (cell.name)
      items.push({ kind: 'cell', id: cell.id, text: cell.name })
  }
  return items
}

export function sameCells(
  a: ReadonlyArray<NotebookCell>,
  b: ReadonlyArray<NotebookCell>,
) {
  return JSON.stringify(a) === JSON.stringify(b)
}

/** Names a new notebook file from what the person typed. */
export function notebookFileName(input: string): string | null {
  const trimmed = input.trim().replace(/\.ipynb$/, '')
  const path = trimmed
    .split('/')
    .map((part) =>
      part
        .trim()
        .replace(/\s+/g, '-')
        .replace(/[^A-Za-z0-9_.-]/g, ''),
    )
    .filter(Boolean)
  if (path.length === 0 || path.some((part) => !/^[A-Za-z0-9_]/.test(part)))
    return null
  return `${path.join('/')}.ipynb`
}

export type TreeNode = {
  name: string
  path: string
  children: Array<TreeNode>
  file: boolean
}

export function notebookTree(paths: ReadonlyArray<string>): Array<TreeNode> {
  const root: Array<TreeNode> = []
  for (const path of paths) {
    let level = root
    const parts = path.split('/')
    parts.forEach((part, index) => {
      const file = index === parts.length - 1
      const at = parts.slice(0, index + 1).join('/')
      let node = level.find((item) => item.name === part && item.file === file)
      if (!node) {
        node = { name: part, path: at, children: [], file }
        level.push(node)
      }
      level = node.children
    })
  }
  const sort = (nodes: Array<TreeNode>) => {
    nodes.sort((a, b) =>
      a.file === b.file ? a.name.localeCompare(b.name) : a.file ? 1 : -1,
    )
    for (const node of nodes) sort(node.children)
  }
  sort(root)
  return root
}
