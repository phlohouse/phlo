/** Keeps query drafts in this browser session without writing SQL or rows to storage. */
import type { QuerySession, SavedQuery } from './data/api/query'

export type QueryTable = {
  catalog: string
  schema: string
  table: string
}

export type QueryTab = {
  id: string
  name: string
  sql: string
  saved?: SavedQuery
  session?: QuerySession
  submittedSql?: string
  mode: 'results' | 'plan'
  error?: string
  pinnedIncident?: string
  dirty?: boolean
  previewTable?: QueryTable
}

export type QueryWorkspace<T> = {
  tabs: Array<T>
  activeId: string
  selected: QueryTable | null
}

const workspaces = new Map<
  string,
  { actor: string; workspace: QueryWorkspace<QueryTab> }
>()

export function queryWorkspaceKey(env: string, actor: string) {
  return JSON.stringify([env, actor])
}

export function queryTableName(table: QueryTable) {
  return `${table.catalog}.${table.schema}.${table.table}`
}

export function queryTableSql(table: QueryTable) {
  const quoteIdentifier = (identifier: string) =>
    `"${identifier.replaceAll('"', '""')}"`
  return `SELECT *\nFROM ${[table.catalog, table.schema, table.table]
    .map(quoteIdentifier)
    .join('.')}\nLIMIT 100`
}

export function openQueryTablePreview(
  workspace: QueryWorkspace<QueryTab>,
  table: QueryTable,
  id: string,
) {
  const existing = workspace.tabs.find(
    (tab) =>
      tab.previewTable?.catalog === table.catalog &&
      tab.previewTable.schema === table.schema &&
      tab.previewTable.table === table.table,
  )
  return {
    tabs: existing
      ? workspace.tabs
      : [
          ...workspace.tabs,
          {
            id,
            name: `Query · ${queryTableName(table)}`,
            sql: queryTableSql(table),
            mode: 'results' as const,
            previewTable: table,
          },
        ],
    activeId: existing?.id ?? id,
    selected: table,
  }
}

export function isCurrentQueryResponse(
  active: boolean,
  env: string,
  responseEnv: string,
) {
  return active && responseEnv === env
}

export function queryDraftChangedSinceAttempt(
  tab: Pick<QueryTab, 'sql' | 'submittedSql'>,
) {
  return tab.submittedSql !== undefined && tab.sql !== tab.submittedSql
}

export function restoreQueryWorkspace(key: string) {
  if (typeof window === 'undefined') return undefined
  return workspaces.get(key)?.workspace
}

export function storeQueryWorkspace(
  key: string,
  actor: string,
  workspace: QueryWorkspace<QueryTab>,
) {
  if (typeof window === 'undefined') return
  workspaces.set(key, { actor, workspace })
}

export function clearQueryWorkspacesForOtherActors(actor: string) {
  if (typeof window === 'undefined') return
  for (const [key, value] of workspaces) {
    if (value.actor !== actor) workspaces.delete(key)
  }
}

export function clearQueryWorkspaces() {
  if (typeof window === 'undefined') return
  workspaces.clear()
}

export function queryInitialSql(
  supplied: string | undefined,
  fallback: string,
) {
  return supplied ?? fallback
}

export function renameQueryTab(
  tab: { name: string },
  value: string,
  save: boolean,
) {
  const name = value.trim()
  return save && name && name !== tab.name ? { name, dirty: true } : {}
}

export function closeQueryTab<T extends { id: string }>(
  tabs: Array<T>,
  activeId: string,
  closingId: string,
  createFallback: () => T,
) {
  const index = tabs.findIndex((tab) => tab.id === closingId)
  const remaining = tabs.filter((tab) => tab.id !== closingId)
  const active = tabs[index]
  const nextTabs = remaining.length ? remaining : [createFallback()]
  const nextActive =
    activeId !== closingId
      ? activeId
      : (remaining[Math.max(0, index - 1)] ?? nextTabs[0]).id
  return { tabs: nextTabs, activeId: nextActive, closed: active }
}

export function nextQueryTabFocus(key: string, count: number, current: number) {
  if (!count) return -1
  if (key === 'Home') return 0
  if (key === 'End') return count - 1
  if (key === 'ArrowRight') return (current + 1) % count
  if (key === 'ArrowLeft') return (current - 1 + count) % count
  return current
}
