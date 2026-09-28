/**
 * Nessie Server Functions
 *
 * Thin wrappers that forward to phlo-api (Python backend).
 * Preserves SSR while keeping business logic in Python.
 */

import { createMiddleware, createServerFn } from '@tanstack/react-start'

import type { ObservatoryEnvironment } from '@/observatory/api/environment'
import { v1Endpoint } from '@/observatory/api/environment'
import { authMiddleware } from '@/observatory/api/auth'
import { cacheKeys, cacheTTL, withCache } from '@/server/cache'
import { apiGet } from '@/server/phlo-api'

function bearerAuthorization(value: string | null): string | undefined {
  return value !== null && /^Bearer\s+\S+$/i.test(value) ? value : undefined
}

const nessieReadAuthorization = createMiddleware({ type: 'request' }).server(
  ({ next, request }) =>
    next({
      context: {
        authorization: bearerAuthorization(
          request.headers.get('authorization'),
        ),
      },
    }),
)

// Types for Nessie data structures
export interface Branch {
  type: 'BRANCH' | 'TAG'
  name: string
  hash: string
}

export interface NessieConfig {
  connected: boolean
  error?: string
  defaultBranch?: string
}

interface BranchRequest {
  env?: ObservatoryEnvironment
}

interface ApiBranch {
  name: string
  type: 'BRANCH'
  hash: string
  protected: boolean
}

interface ApiBranchList {
  env: 'prod' | 'staging'
  items: Array<ApiBranch>
}

function transformBranch(branch: ApiBranch): Branch {
  return {
    type: branch.type,
    name: branch.name,
    hash: branch.hash,
  }
}

/**
 * Check if Nessie is reachable
 */
export const checkNessieConnection = createServerFn()
  .middleware([authMiddleware, nessieReadAuthorization])
  .inputValidator((input: BranchRequest) => input)
  .handler(async ({ context, data }): Promise<NessieConfig> => {
    try {
      const endpoint = v1Endpoint('/api/v1/branches', data.env)
      const result = await withCache(
        () =>
          apiGet<ApiBranchList>(
            endpoint,
            undefined,
            30000,
            context.authorization,
          ),
        `${cacheKeys.nessieConnection()}:${endpoint}`,
        cacheTTL.nessieConnection,
      )
      return {
        connected: true,
        defaultBranch: result.items[0]?.name,
      }
    } catch (error) {
      return {
        connected: false,
        error: error instanceof Error ? error.message : 'Unknown error',
      }
    }
  })

/**
 * Get all branches and tags
 */
export const getBranches = createServerFn()
  .middleware([authMiddleware, nessieReadAuthorization])
  .inputValidator((input: BranchRequest) => input)
  .handler(
    async ({ context, data }): Promise<Array<Branch> | { error: string }> => {
      try {
        const endpoint = v1Endpoint('/api/v1/branches', data.env)
        const result = await withCache(
          () =>
            apiGet<ApiBranchList>(
              endpoint,
              undefined,
              30000,
              context.authorization,
            ),
          `${cacheKeys.nessieBranches()}:${endpoint}`,
          cacheTTL.nessieBranches,
        )
        return result.items.map(transformBranch)
      } catch (error) {
        return {
          error: error instanceof Error ? error.message : 'Unknown error',
        }
      }
    },
  )
