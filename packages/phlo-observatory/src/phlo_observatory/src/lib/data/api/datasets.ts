import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { phloApi } from './client'

const datasetSchema = z
  .object({
    dataset_id: z.string(),
    table_id: z.string(),
    candidate: z.boolean(),
    owner: z.string().nullable(),
    classifications: z.array(z.string()),
    workflow_state: z.string().nullable(),
    publication_state: z.string().nullable(),
    approval_state: z.string().nullable(),
    declared: z.boolean(),
    readiness: z
      .object({
        action: z.string(),
        ready: z.boolean(),
        policy_version: z.string(),
        reasons: z.array(z.string()),
      })
      .catchall(z.json()),
    allowed_transitions: z.array(z.string()),
  })
  .catchall(z.json())

export const getDatasetInventory = createServerFn({ method: 'GET' }).handler(
  () =>
    phloApi(
      '/api/v1/datasets?limit=500',
      z.object({
        scope: z.literal('project'),
        source: z.literal('core_dataset_authority'),
        items: z.array(datasetSchema),
        truncated: z.boolean(),
      }),
    ),
)
