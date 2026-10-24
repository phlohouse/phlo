/** Coarse action preflight. Mutations still enforce contextual authorization. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

const contextSchema = z.object({
  job_id: z.string().optional(),
  run_id: z.string().optional(),
  branch_name: z.string().optional(),
})
export type ActionContext = z.infer<typeof contextSchema>
export const actionAvailabilitySchema = z.object({
  env: environmentSchema,
  actions: z.record(
    z.string(),
    z.object({
      allowed: z.boolean(),
      reason: z.string().nullable(),
    }),
  ),
})
export type ActionAvailability = z.infer<typeof actionAvailabilitySchema>
export const getActionAvailability = createServerFn({ method: 'GET' })
  .inputValidator(contextSchema.extend({ env: environmentSchema }))
  .handler(({ data }) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(data))
      if (value !== undefined) params.set(key, value)
    return phloApi(
      `api/v1/action-availability?${params}`,
      actionAvailabilitySchema,
      { env: data.env },
    )
  })
