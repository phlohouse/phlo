/** Reads the Phlo Transform workspace inventory declared by transform assets. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

export const transformRunSchema = z.object({
  status: z.string().nullable().optional(),
  action: z.string().nullable().optional(),
  reasons: z.array(z.string()).optional(),
  run_id: z.string().nullable().optional(),
  plan_id: z.string().nullable().optional(),
  duration_ms: z.number().int().nonnegative().nullable().optional(),
  counts: z.record(z.string(), z.number().int().nonnegative()).optional(),
  previous_version: z.string().nullable().optional(),
  desired_version: z.string().nullable().optional(),
  sql_hash: z.string().nullable().optional(),
  query_id: z.string().nullable().optional(),
})

const transformModelSchema = z.object({
  asset_id: z.string(),
  key: z.array(z.string()),
  model: z.string(),
  target: z.string().nullable().optional(),
  materialization: z.string().nullable().optional(),
  version: z.string().nullable().optional(),
  path: z.string().nullable().optional(),
  tags: z.array(z.string()).optional(),
  dependencies: z.array(z.array(z.string())),
  sources: z.array(z.string()).optional(),
  source_assets: z.array(z.string()).optional(),
  tests: z.array(z.string()).optional(),
  last_run: transformRunSchema.nullable().optional(),
})

const transformWorkspaceSchema = z.object({
  env: environmentSchema,
  status: z.enum(['available', 'unavailable']),
  engine: z.string().default('phlo-transform'),
  reason: z.string().nullable().optional(),
  workspace: z.string().nullable().optional(),
  model_count: z.number().int().nonnegative(),
  test_count: z.number().int().nonnegative(),
  source_count: z.number().int().nonnegative(),
  models: z.array(transformModelSchema),
})

export type TransformModel = z.infer<typeof transformModelSchema>
export type TransformWorkspace = z.infer<typeof transformWorkspaceSchema>

export const getTransformWorkspace = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(async ({ data: env }) => {
    return phloApi(
      `api/v1/transform?env=${encodeURIComponent(env)}`,
      transformWorkspaceSchema,
      { env },
    )
  })
