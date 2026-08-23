/**
 * Saved Queries Storage
 *
 * localStorage-based persistence for saved queries.
 * Uses versioned schema for future migration support.
 */

import { z } from 'zod'

// Storage keys
const SAVED_QUERIES_KEY = 'phlo-observatory-saved-queries-v1'

// Schemas
const savedQuerySchema = z.object({
  id: z.string(),
  name: z.string().min(1),
  query: z.string().min(1),
  description: z.string().optional(),
  tags: z.array(z.string()).optional(),
  branch: z.string().optional(),
  createdAt: z.string(),
  updatedAt: z.string(),
})

const savedQueriesStoreSchema = z.object({
  version: z.literal(1),
  queries: z.array(savedQuerySchema),
})

// Types
export type SavedQuery = z.infer<typeof savedQuerySchema>

type SavedQueriesStore = z.infer<typeof savedQueriesStoreSchema>

// Create inputs (without generated fields)
export type CreateQueryInput = Omit<
  SavedQuery,
  'id' | 'createdAt' | 'updatedAt'
>

// Helper functions
function getEmptyQueriesStore(): SavedQueriesStore {
  return { version: 1, queries: [] }
}

// An unreadable or schema-invalid store resets to empty instead of throwing,
// so callers cannot distinguish "no saved queries" from corrupted data.
// The versioned schema exists to keep this migration path open.
function loadQueriesStore(): SavedQueriesStore {
  if (typeof window === 'undefined') return getEmptyQueriesStore()

  const raw = window.localStorage.getItem(SAVED_QUERIES_KEY)
  if (!raw) return getEmptyQueriesStore()

  try {
    const parsed = savedQueriesStoreSchema.safeParse(JSON.parse(raw))
    return parsed.success ? parsed.data : getEmptyQueriesStore()
  } catch {
    return getEmptyQueriesStore()
  }
}

function saveQueriesStore(store: SavedQueriesStore): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(SAVED_QUERIES_KEY, JSON.stringify(store))
}

// Saved Queries CRUD
export function getSavedQueries(): Array<SavedQuery> {
  return loadQueriesStore().queries
}

export function getSavedQueryById(id: string): SavedQuery | undefined {
  return getSavedQueries().find((q) => q.id === id)
}

export function createSavedQuery(input: CreateQueryInput): SavedQuery {
  const store = loadQueriesStore()
  const now = new Date().toISOString()
  const query: SavedQuery = {
    ...input,
    id: crypto.randomUUID(),
    createdAt: now,
    updatedAt: now,
  }
  store.queries.push(query)
  saveQueriesStore(store)
  return query
}

export function updateSavedQuery(
  id: string,
  updates: Partial<Omit<SavedQuery, 'id' | 'createdAt'>>,
): SavedQuery | undefined {
  const store = loadQueriesStore()
  const index = store.queries.findIndex((q) => q.id === id)
  if (index === -1) return undefined

  store.queries[index] = {
    ...store.queries[index],
    ...updates,
    updatedAt: new Date().toISOString(),
  }
  saveQueriesStore(store)
  return store.queries[index]
}

export function deleteSavedQuery(id: string): boolean {
  const store = loadQueriesStore()
  const initialLength = store.queries.length
  store.queries = store.queries.filter((q) => q.id !== id)
  if (store.queries.length < initialLength) {
    saveQueriesStore(store)
    return true
  }
  return false
}
