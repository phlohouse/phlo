/** Open notebook tabs and the unsaved work in each, kept while the person moves between them. */
import type { CellState, NotebookCell } from '@/lib/notebook'

/** What a tab keeps when another notebook is in front: edits, results and the version it was opened at. */
export type NotebookDraft = {
  cells: Array<NotebookCell>
  packages: Array<string>
  saved: { cells: Array<NotebookCell>; packages: Array<string>; digest: string }
  states: Record<string, CellState>
}

const drafts = new Map<string, NotebookDraft>()
const listeners = new Set<() => void>()
const MAX_TABS = 12

function key(env: string, path: string) {
  return `${env}\u0000${path}`
}

function storageKey(env: string) {
  return `phlo.notebooks.tabs.${env}`
}

function notify() {
  for (const listener of listeners) listener()
}

export function subscribeTabs(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function readDraft(env: string, path: string) {
  return drafts.get(key(env, path))
}

export function keepDraft(env: string, path: string, draft: NotebookDraft) {
  drafts.set(key(env, path), draft)
  notify()
}

export function dropDraft(env: string, path: string) {
  if (drafts.delete(key(env, path))) notify()
}

export function isDirty(draft: NotebookDraft | undefined) {
  if (!draft) return false
  return (
    JSON.stringify(draft.cells) !== JSON.stringify(draft.saved.cells) ||
    JSON.stringify(draft.packages) !== JSON.stringify(draft.saved.packages)
  )
}

/** Notebooks with edits that are not saved, in any open tab. */
export function dirtyPaths(env: string): Array<string> {
  const prefix = `${env}\u0000`
  return [...drafts.entries()]
    .filter(([name, draft]) => name.startsWith(prefix) && isDirty(draft))
    .map(([name]) => name.slice(prefix.length))
}

export function readTabs(env: string): Array<string> {
  try {
    const raw: unknown = JSON.parse(
      globalThis.localStorage.getItem(storageKey(env)) ?? '[]',
    )
    return Array.isArray(raw)
      ? raw.filter((item): item is string => typeof item === 'string')
      : []
  } catch {
    return []
  }
}

export function writeTabs(env: string, tabs: ReadonlyArray<string>) {
  try {
    globalThis.localStorage.setItem(
      storageKey(env),
      JSON.stringify(tabs.slice(-MAX_TABS)),
    )
  } catch {
    // Tabs are a convenience; without storage they last as long as the page.
  }
  notify()
}

/** The tab list with `path` added at the end, unless it is already open. */
export function withTab(tabs: ReadonlyArray<string>, path: string) {
  return tabs.includes(path) ? [...tabs] : [...tabs, path].slice(-MAX_TABS)
}

/** Follows a rename: the tab and its draft move to the new path. */
export function renameTab(env: string, from: string, to: string) {
  writeTabs(
    env,
    readTabs(env).map((item) => (item === from ? to : item)),
  )
  const draft = drafts.get(key(env, from))
  if (draft) {
    drafts.delete(key(env, from))
    drafts.set(key(env, to), draft)
  }
  notify()
}
