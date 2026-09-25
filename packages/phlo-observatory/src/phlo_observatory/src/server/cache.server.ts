/**
 * Cache stats endpoint - see ADR 0019-observatory-metadata-caching.md
 */

import { createServerFn } from '@tanstack/react-start'

import { clearCache, getCacheStats } from './cache'

import { authMiddleware } from '@/observatory/api/auth'
import { mutationAuthorization } from '@/server/authenticated-mutation'

export const getCacheStatsEndpoint = createServerFn()
  .middleware([authMiddleware])
  .handler(() => {
    return Promise.resolve(getCacheStats())
  })

export const clearCacheEndpoint = createServerFn()
  .middleware([mutationAuthorization, authMiddleware])
  .handler(() => {
    clearCache()
    return Promise.resolve({ cleared: true })
  })
