/**
 * Browser-local activity state for the shell: recent visits, query execution
 * history, and query workspace tabs persisted to localStorage.
 */
export interface ObservatoryRecentVisit {
  path: string
  label: string
  visitedAt: string
}

export interface ObservatoryQueryExecution {
  id: string
  sql: string
  status: 'succeeded' | 'failed'
  startedAt: string
  durationMs: number
  rowCount: number
  error?: string
}

export interface ObservatoryQueryWorkspaceTab {
  id: string
  name: string
  sql: string
  savedQueryId?: string
}

export interface ObservatoryQueryWorkspace {
  activeId: string
  tabs: Array<ObservatoryQueryWorkspaceTab>
}

const recentVisitsKey = 'phlo-observatory-recent-visits'
const queryHistoryKey = 'phlo-observatory-query-history'
const queryWorkspaceKey = 'phlo-observatory-query-workspace'
export const localActivityEvent = 'phlo-observatory-local-activity'

export function readRecentVisits(): Array<ObservatoryRecentVisit> {
  return readList<ObservatoryRecentVisit>(recentVisitsKey).filter(
    (visit) => visit.path !== '/recents',
  )
}

export function recordRecentVisit(path: string, label: string): void {
  if (typeof window === 'undefined') return
  if (path === '/recents') return
  const next = [
    { path, label, visitedAt: new Date().toISOString() },
    ...readRecentVisits().filter((visit) => visit.path !== path),
  ].slice(0, 30)
  writeList(recentVisitsKey, next)
}

export function readQueryHistory(): Array<ObservatoryQueryExecution> {
  return readList<ObservatoryQueryExecution>(queryHistoryKey)
}

export function recordQueryExecution(
  execution: ObservatoryQueryExecution,
): void {
  if (typeof window === 'undefined') return
  writeList(queryHistoryKey, [execution, ...readQueryHistory()].slice(0, 100))
}

export function readQueryWorkspace(): ObservatoryQueryWorkspace {
  if (typeof window === 'undefined') return defaultQueryWorkspace()
  try {
    const value = JSON.parse(
      window.localStorage.getItem(queryWorkspaceKey) ?? '',
    )
    if (!value || !Array.isArray(value.tabs) || !value.tabs.length) {
      return defaultQueryWorkspace()
    }
    return {
      activeId: value.tabs.some(
        (tab: ObservatoryQueryWorkspaceTab) => tab.id === value.activeId,
      )
        ? value.activeId
        : value.tabs[0].id,
      tabs: value.tabs,
    }
  } catch {
    return defaultQueryWorkspace()
  }
}

export function writeQueryWorkspace(
  workspace: ObservatoryQueryWorkspace,
): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(queryWorkspaceKey, JSON.stringify(workspace))
}

function defaultQueryWorkspace(): ObservatoryQueryWorkspace {
  return {
    activeId: 'scratch-1',
    tabs: [{ id: 'scratch-1', name: 'Untitled query', sql: '' }],
  }
}

function readList<T>(key: string): Array<T> {
  if (typeof window === 'undefined') return []
  try {
    const value = JSON.parse(window.localStorage.getItem(key) ?? '[]')
    return Array.isArray(value) ? (value as Array<T>) : []
  } catch {
    return []
  }
}

// 'storage' events only fire in other tabs, so writers also dispatch a
// custom event for same-page listeners that re-read localStorage.
function writeList<T>(key: string, value: Array<T>): void {
  window.localStorage.setItem(key, JSON.stringify(value))
  window.dispatchEvent(new Event(localActivityEvent))
}
