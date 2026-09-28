/** Environment-scoped branch review reads backed by the v1 branch contracts. */
import { createMiddleware, createServerFn } from '@tanstack/react-start'
import { z } from 'zod'

import { v1Endpoint } from '@/observatory/api/environment'
import { apiGet } from '@/server/phlo-api'

const referenceSchema = z.object({
  env: z.enum(['prod', 'staging']),
  name: z.string().min(1),
  type: z.enum(['BRANCH', 'TAG']),
  hash: z.string().min(1),
  protected: z.boolean(),
})

const refsSchema = z.object({
  env: z.enum(['prod', 'staging']),
  items: z.array(referenceSchema),
})
const commitsSchema = z.object({
  env: z.enum(['prod', 'staging']),
  branch: z.string().min(1),
  items: z.array(
    z.object({
      hash: z.string().min(1),
      parent_hashes: z.array(z.string()),
      message: z.string().nullable(),
      author: z.string().nullable(),
      committer: z.string().nullable(),
      committed_at: z.string().nullable(),
    }),
  ),
  next_cursor: z.string().nullable(),
})
const comparisonSchema = z.object({
  env: z.enum(['prod', 'staging']),
  source: z.string(),
  target: z.string(),
  source_hash: z.string(),
  target_hash: z.string(),
  merge_base: z.string().nullable(),
  ahead: z.number().int().nullable(),
  behind: z.number().int().nullable(),
  status: z.enum(['compared', 'unavailable']),
})
const diffSchema = z.object({
  env: z.enum(['prod', 'staging']),
  source: z.string(),
  target: z.string(),
  source_hash: z.string(),
  target_hash: z.string(),
  truncated: z.boolean(),
  items: z.array(
    z.object({
      key: z.string(),
      status: z.enum(['added', 'modified', 'deleted']),
      from_content_id: z.string().nullable(),
      to_content_id: z.string().nullable(),
    }),
  ),
})

export type BranchReference = z.infer<typeof referenceSchema>
export type BranchCommitPage = z.infer<typeof commitsSchema>
export type BranchComparison = z.infer<typeof comparisonSchema>
export type BranchDiff = z.infer<typeof diffSchema>
export type BranchRead<T> =
  | { kind: 'available'; data: T }
  | { kind: 'unavailable'; message: string }

function bearerAuthorization(value: string | null): string | undefined {
  return value !== null && /^Bearer\s+\S+$/i.test(value) ? value : undefined
}

const readAuthorization = createMiddleware({ type: 'request' }).server(
  ({ next, request }) =>
    next({
      context: {
        authorization: bearerAuthorization(
          request.headers.get('authorization'),
        ),
      },
    }),
)

function unavailable<T>(error: unknown): BranchRead<T> {
  return {
    kind: 'unavailable',
    message:
      error instanceof Error ? error.message : 'Branch data is unavailable.',
  }
}

function branchPath(name: string): string {
  return encodeURIComponent(name)
}

export const getBranchReferences = createServerFn()
  .middleware([readAuthorization])
  .inputValidator(z.object({ env: z.enum(['prod', 'staging']) }))
  .handler(
    async ({ context, data }): Promise<BranchRead<Array<BranchReference>>> => {
      try {
        const result = refsSchema.parse(
          await apiGet<unknown>(
            v1Endpoint('/api/v1/branches/refs', data.env),
            undefined,
            8000,
            context.authorization,
          ),
        )
        if (result.env !== data.env) {
          throw new Error('phlo-api returned refs for another environment.')
        }
        return {
          kind: 'available',
          data: result.items,
        }
      } catch (error) {
        return unavailable(error)
      }
    },
  )

export const getBranchCommits = createServerFn()
  .middleware([readAuthorization])
  .inputValidator(
    z.object({ env: z.enum(['prod', 'staging']), branch: z.string().min(1) }),
  )
  .handler(async ({ context, data }): Promise<BranchRead<BranchCommitPage>> => {
    try {
      const result = commitsSchema.parse(
        await apiGet<unknown>(
          v1Endpoint(
            `/api/v1/branches/${branchPath(data.branch)}/commits?limit=50`,
            data.env,
          ),
          undefined,
          8000,
          context.authorization,
        ),
      )
      if (result.env !== data.env || result.branch !== data.branch) {
        throw new Error(
          'phlo-api returned commits for another branch or environment.',
        )
      }
      return { kind: 'available', data: result }
    } catch (error) {
      return unavailable(error)
    }
  })

export const getBranchComparison = createServerFn()
  .middleware([readAuthorization])
  .inputValidator(
    z.object({
      env: z.enum(['prod', 'staging']),
      branch: z.string().min(1),
      target: z.string().min(1),
    }),
  )
  .handler(async ({ context, data }): Promise<BranchRead<BranchComparison>> => {
    try {
      const endpoint = v1Endpoint(
        `/api/v1/branches/${branchPath(data.branch)}/compare?target=${branchPath(data.target)}`,
        data.env,
      )
      const comparison = comparisonSchema.parse(
        await apiGet<unknown>(endpoint, undefined, 8000, context.authorization),
      )
      if (
        comparison.env !== data.env ||
        comparison.source !== data.branch ||
        comparison.target !== data.target
      ) {
        throw new Error(
          'phlo-api returned a comparison for another branch or environment.',
        )
      }
      return { kind: 'available', data: comparison }
    } catch (error) {
      return unavailable(error)
    }
  })

export const getBranchDiff = createServerFn()
  .middleware([readAuthorization])
  .inputValidator(
    z.object({
      env: z.enum(['prod', 'staging']),
      branch: z.string().min(1),
      target: z.string().min(1),
    }),
  )
  .handler(async ({ context, data }): Promise<BranchRead<BranchDiff>> => {
    try {
      const endpoint = v1Endpoint(
        `/api/v1/branches/${branchPath(data.branch)}/diff?target=${branchPath(data.target)}`,
        data.env,
      )
      const diff = diffSchema.parse(
        await apiGet<unknown>(endpoint, undefined, 8000, context.authorization),
      )
      if (
        diff.env !== data.env ||
        diff.source !== data.branch ||
        diff.target !== data.target
      ) {
        throw new Error(
          'phlo-api returned a diff for another branch or environment.',
        )
      }
      return { kind: 'available', data: diff }
    } catch (error) {
      return unavailable(error)
    }
  })
