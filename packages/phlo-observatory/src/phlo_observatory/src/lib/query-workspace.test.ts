/** Verifies query drafts, tabs, and responses remain scoped to their workspace. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearQueryWorkspaces,
  clearQueryWorkspacesForOtherActors,
  closeQueryTab,
  isCurrentQueryResponse,
  nextQueryTabFocus,
  openQueryTablePreview,
  queryDraftChangedSinceAttempt,
  queryInitialSql,
  queryTableSql,
  queryWorkspaceKey,
  renameQueryTab,
  restoreQueryWorkspace,
  storeQueryWorkspace,
} from './query-workspace'
import type { QueryTab } from './query-workspace'

const tab = (id: string, sql: string): QueryTab => ({
  id,
  name: `Query ${id}`,
  sql,
  mode: 'results',
})
const table = { catalog: 'iceberg', schema: 'silver', table: 'orders' }

afterEach(() => vi.unstubAllGlobals())

describe('query workspace', () => {
  it('restores drafts only for the same environment and actor', () => {
    vi.stubGlobal('window', {})
    const key = queryWorkspaceKey('prod', 'qa-user')
    const workspace = {
      tabs: [tab('one', 'SELECT 42')],
      activeId: 'one',
      selected: table,
    }
    storeQueryWorkspace(key, 'qa-user', workspace)

    expect(restoreQueryWorkspace(key)).toEqual(workspace)
    expect(restoreQueryWorkspace(queryWorkspaceKey('staging', 'qa-user'))).toBe(
      undefined,
    )
    expect(restoreQueryWorkspace(queryWorkspaceKey('prod', 'other-user'))).toBe(
      undefined,
    )
  })

  it('never reads or retains drafts during server rendering', () => {
    const key = queryWorkspaceKey('prod', 'server-user')
    storeQueryWorkspace(key, 'server-user', {
      tabs: [tab('private', 'SELECT secret')],
      activeId: 'private',
      selected: null,
    })
    expect(restoreQueryWorkspace(key)).toBeUndefined()

    vi.stubGlobal('window', {})
    expect(restoreQueryWorkspace(key)).toBeUndefined()
  })

  it('drops cached SQL for actors other than the authenticated shell identity', () => {
    vi.stubGlobal('window', {})
    const oldActor = JSON.stringify(['user', 'same-subject'])
    const currentActor = JSON.stringify(['service', 'same-subject'])
    const oldKey = queryWorkspaceKey('prod', oldActor)
    const currentKey = queryWorkspaceKey('staging', currentActor)
    storeQueryWorkspace(oldKey, oldActor, {
      tabs: [tab('old', 'SELECT old')],
      activeId: 'old',
      selected: null,
    })
    storeQueryWorkspace(currentKey, currentActor, {
      tabs: [tab('current', 'SELECT current')],
      activeId: 'current',
      selected: null,
    })

    clearQueryWorkspacesForOtherActors(currentActor)

    expect(restoreQueryWorkspace(oldKey)).toBeUndefined()
    expect(restoreQueryWorkspace(currentKey)?.tabs[0]?.sql).toBe(
      'SELECT current',
    )
    clearQueryWorkspaces()
    expect(restoreQueryWorkspace(currentKey)).toBeUndefined()
  })

  it('moves tab focus with wraparound and Home/End', () => {
    expect(nextQueryTabFocus('ArrowRight', 3, 2)).toBe(0)
    expect(nextQueryTabFocus('ArrowLeft', 3, 0)).toBe(2)
    expect(nextQueryTabFocus('Home', 3, 2)).toBe(0)
    expect(nextQueryTabFocus('End', 3, 0)).toBe(2)
    expect(nextQueryTabFocus('Enter', 3, 1)).toBe(1)
  })

  it('ignores responses after unmount or from a different environment', () => {
    expect(isCurrentQueryResponse(true, 'prod', 'prod')).toBe(true)
    expect(isCurrentQueryResponse(true, 'prod', 'staging')).toBe(false)
    expect(isCurrentQueryResponse(false, 'prod', 'prod')).toBe(false)
  })

  it('marks previous attempts stale only when their submitted SQL differs', () => {
    expect(queryDraftChangedSinceAttempt(tab('new', 'SELECT 7'))).toBe(false)
    const attempted = {
      ...tab('attempted', 'SELECT 7'),
      submittedSql: 'SELECT 7',
    }
    const renamed = { ...attempted, dirty: true }
    expect(queryDraftChangedSinceAttempt(renamed)).toBe(false)
    expect(
      queryDraftChangedSinceAttempt({ ...attempted, sql: 'SELECT 9' }),
    ).toBe(true)
    expect(queryDraftChangedSinceAttempt({ ...attempted, sql: '' })).toBe(true)
    expect(
      queryDraftChangedSinceAttempt({
        ...attempted,
        submittedSql: 'SELECT 9',
        sql: 'SELECT 9',
      }),
    ).toBe(false)
  })

  it('uses supplied SQL verbatim and falls back only when it is absent', () => {
    expect(queryInitialSql('  SELECT 1;  ', 'SELECT 2')).toBe('  SELECT 1;  ')
    expect(queryInitialSql('', 'SELECT 2')).toBe('')
    expect(queryInitialSql(undefined, 'SELECT 2')).toBe('SELECT 2')
  })

  it('opens a safely quoted preview without replacing dirty or running tabs', () => {
    const initialPreview = {
      ...tab('initial', 'SELECT edited'),
      dirty: true,
      previewTable: table,
    }
    const selectedExisting = openQueryTablePreview(
      { tabs: [initialPreview], activeId: initialPreview.id, selected: table },
      table,
      'unused-preview',
    )
    expect(selectedExisting.tabs).toEqual([initialPreview])
    expect(selectedExisting.activeId).toBe('initial')
    expect(selectedExisting.tabs[0]?.sql).toBe('SELECT edited')

    const dirtyTab = {
      ...tab('draft', 'SELECT private_value'),
      dirty: true,
      session: {
        id: 'session-1',
        env: 'prod',
        nessie_ref: 'main',
        engine: 'trino',
        evidence_available: false,
        status: 'completed',
        sql_hash: 'hash',
        created_at: '',
        updated_at: '',
        result: null,
        error: null,
      } satisfies NonNullable<QueryTab['session']>,
    }
    const workspace = {
      tabs: [dirtyTab],
      activeId: dirtyTab.id,
      selected: null,
    }
    const quotedTable = {
      catalog: 'ice"berg',
      schema: 'silver.schema',
      table: 'orders"; DROP TABLE users; --',
    }
    const opened = openQueryTablePreview(workspace, quotedTable, 'preview-1')
    const preview = opened.tabs.find((item) => item.previewTable)

    expect(opened.activeId).toBe('preview-1')
    expect(opened.tabs[0]).toEqual(dirtyTab)
    expect(opened.selected).toEqual(quotedTable)
    expect(preview).toMatchObject({
      name: 'Query · ice"berg.silver.schema.orders"; DROP TABLE users; --',
      sql: 'SELECT *\nFROM "ice""berg"."silver.schema"."orders""; DROP TABLE users; --"\nLIMIT 100',
      mode: 'results',
      previewTable: quotedTable,
    })
    expect(preview?.session).toBeUndefined()
    expect(queryTableSql(quotedTable)).toContain('"ice""berg"')

    const editedPreview = { ...preview!, sql: 'SELECT edited', dirty: true }
    const reopened = openQueryTablePreview(
      { ...opened, tabs: [dirtyTab, editedPreview] },
      quotedTable,
      'preview-2',
    )
    expect(reopened.tabs).toEqual([dirtyTab, editedPreview])
    expect(reopened.activeId).toBe('preview-1')
    expect(reopened.tabs[1]?.sql).toBe('SELECT edited')
  })

  it('renames without changing the SQL or any execution state', () => {
    const savedTab = { ...tab('saved', 'SELECT 1'), name: 'Old name' }
    expect(renameQueryTab(savedTab, '  Revenue by month  ', true)).toEqual({
      name: 'Revenue by month',
      dirty: true,
    })
    expect(renameQueryTab(savedTab, '   ', true)).toEqual({})
    expect(renameQueryTab(savedTab, 'New name', false)).toEqual({})
  })

  it('selects a neighbour when closing the active tab and supports the last tab', () => {
    const tabs = [tab('a', ''), tab('b', ''), tab('c', '')]
    expect(closeQueryTab(tabs, 'b', 'b', () => tab('fresh', ''))).toMatchObject(
      {
        tabs: [tab('a', ''), tab('c', '')],
        activeId: 'a',
        closed: tab('b', ''),
      },
    )
    expect(
      closeQueryTab([tab('a', '')], 'a', 'a', () => tab('fresh', '')),
    ).toMatchObject({
      tabs: [tab('fresh', '')],
      activeId: 'fresh',
      closed: tab('a', ''),
    })
  })
})
