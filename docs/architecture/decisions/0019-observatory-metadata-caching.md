# 19. Observatory metadata caching

Date: 2025-12-17

## Status

Accepted

## Context

Observatory server functions (`iceberg.server.ts`, `dagster.server.ts`, `search.server.ts`) query Trino
and Dagster GraphQL directly on every request. This causes:

1. **Latency**: Each `getTables` call executes 6+ `SHOW TABLES` queries against Trino (one per schema).
2. **Load**: Repeated navigation across the same tables re-queries metadata every time.
3. **No observability**: No visibility into cache performance or query frequency.

Observatory has no caching layer. The TanStack Start server functions (`createServerFn`) run in
the Node.js SSR context and query upstream services directly on every request.

Related beads:

- `phlo-b1w`: Observatory: Metadata caching (this decision)
- `phlo-13a`: Observatory: Table browser improvements (depends on caching)
- `phlo-8aw`: Observatory: Command palette (uses search index)

## Decision

Implement a server-side metadata cache in Observatory with TTL-based expiration and optional
event-based invalidation.

### Cache Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Observatory SSR                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ getTables()  │    │ getAssets()  │    │getSearchIdx()│  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│         │                   │                   │          │
│         └───────────────────┼───────────────────┘          │
│                             ▼                              │
│                   ┌─────────────────┐                      │
│                   │  MetadataCache  │                      │
│                   │  (in-memory)    │                      │
│                   └────────┬────────┘                      │
│                            │ miss                          │
│         ┌──────────────────┼──────────────────┐            │
│         ▼                  ▼                  ▼            │
│    ┌─────────┐       ┌──────────┐       ┌─────────┐       │
│    │  Trino  │       │  Dagster │       │  Nessie │       │
│    └─────────┘       │  GraphQL │       └─────────┘       │
│                      └──────────┘                          │
└─────────────────────────────────────────────────────────────┘
```

### Implementation

1. **Cache module** (`server/cache.ts`):
   - In-memory Map with TTL per entry
   - Key generation from function name + normalized args
   - `get`, `set`, `invalidate`, `invalidatePattern` methods
   - `getStats` for hit/miss metrics

2. **Cache wrapper** (`withCache` HOF):

   ```typescript
   export function withCache<T>(
     fn: () => Promise<T>,
     key: string,
     ttlMs: number = DEFAULT_TTL_MS,
   ): Promise<T>;
   ```

3. **TTL configuration** (environment variables with sensible defaults):
   | Data Type | TTL | Rationale |
   |-----------|-----|-----------|
   | Table list | 5 min | Changes infrequently, okay to be slightly stale |
   | Table schema | 10 min | Schema changes are rare |
   | Asset list | 2 min | Assets may be added during development |
   | Search index | 5 min | Composite of above, balanced freshness |

4. **Cache key structure**:

   ```
   iceberg:tables:{catalog}:{branch}
   iceberg:schema:{catalog}:{schema}:{table}
   dagster:assets:{dagsterUrl}
   search:index:{dagsterUrl}:{trinoUrl}
   ```

5. **Logging**:
   - Log cache hits/misses at debug level
   - Expose `/api/observatory/cache/stats` endpoint for monitoring
   - Track: total hits, total misses, hit rate, entries by prefix

### Event-Based Invalidation (Phase 2)

Defer Dagster event-based invalidation to a follow-up. The TTL approach provides immediate value
and the 2-5 minute staleness is acceptable for metadata. Event-based invalidation adds complexity
(webhook setup, Dagster sensor configuration) for marginal benefit.

If needed later:

- Dagster sensor emits webhook on asset materialization
- Webhook handler calls `cache.invalidatePattern('iceberg:*')` for schema-affecting materializations

### Fallback Behavior

On cache miss or cache error:

1. Execute the underlying query directly
2. Log the cache miss/error
3. Attempt to populate cache with result
4. Return result to caller (never fail due to cache issues)

## Consequences

### Positive

- Repeated table browser navigation is instant after first load
- Command palette search index loads from cache on subsequent opens
- Reduced load on Trino and Dagster during active Observatory sessions
- Cache stats provide visibility into query patterns

### Negative

- Metadata can be stale for up to TTL duration (mitigated by reasonable TTLs)
- In-memory cache is process-local (acceptable for single-instance Observatory)
- Additional code complexity in server functions

### Risks

- Memory growth if cache is unbounded: mitigate with max entry count and LRU eviction
- Cache key collisions: mitigate with structured key format and tests

## Implementation Plan

1. Create `server/cache.ts` with `MetadataCache` class
2. Add `withCache` wrapper function
3. Integrate into `getTables`, `getTableSchema`, `getAssets`, `getSearchIndex`
4. Add cache stats endpoint
5. Add debug logging for cache operations
6. Write tests for cache behavior (TTL, invalidation, fallback)

## Beads

- phlo-b1w: Observatory: Metadata caching (in progress)
