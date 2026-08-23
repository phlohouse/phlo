/**
 * React hook for managing saved queries and views
 */

import { useCallback, useSyncExternalStore } from 'react'

import type { CreateQueryInput, SavedQuery } from '@/lib/savedQueries'
import {
  createSavedQuery,
  deleteSavedQuery,
  getSavedQueries,
  getSavedQueryById,
  updateSavedQuery,
} from '@/lib/savedQueries'

// External store subscription for React 18+
let listeners: Array<() => void> = []

// Cache for stable snapshot references
let cachedQueries: Array<SavedQuery> | null = null
const emptySavedQueries: Array<SavedQuery> = []

function subscribe(callback: () => void): () => void {
  listeners.push(callback)
  return () => {
    listeners = listeners.filter((l) => l !== callback)
  }
}

function notifyListeners(): void {
  // Invalidate caches before notifying
  cachedQueries = null
  listeners.forEach((l) => l())
}

// Wrap mutations to notify listeners
function withNotify<T>(fn: () => T): T {
  const result = fn()
  notifyListeners()
  return result
}

// Snapshot functions - return cached references for stability
function getQueriesSnapshot(): Array<SavedQuery> {
  if (cachedQueries === null) {
    cachedQueries = getSavedQueries()
  }
  return cachedQueries
}

/**
 * Snapshot for server rendering. Must return the same shared reference every
 * call: useSyncExternalStore re-renders endlessly when the server snapshot is
 * a fresh object each time.
 */
function getServerSnapshot(): Array<SavedQuery> {
  return emptySavedQueries
}

/**
 * Hook for managing saved queries
 */
export function useSavedQueries() {
  const queries = useSyncExternalStore(
    subscribe,
    getQueriesSnapshot,
    getServerSnapshot,
  )

  const save = useCallback((input: CreateQueryInput): SavedQuery => {
    return withNotify(() => createSavedQuery(input))
  }, [])

  const update = useCallback(
    (
      id: string,
      updates: Partial<Omit<SavedQuery, 'id' | 'createdAt'>>,
    ): SavedQuery | undefined => {
      return withNotify(() => updateSavedQuery(id, updates))
    },
    [],
  )

  const remove = useCallback((id: string): boolean => {
    return withNotify(() => deleteSavedQuery(id))
  }, [])

  const getById = useCallback((id: string): SavedQuery | undefined => {
    return getSavedQueryById(id)
  }, [])

  return {
    queries,
    saveQuery: save,
    updateQuery: update,
    deleteQuery: remove,
    getQueryById: getById,
  }
}
