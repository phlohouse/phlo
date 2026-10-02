/** Server functions for data shared by the app shell and overview. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import {
  environmentSchema,
  overviewSchema,
  phloApi,
  servicesSchema,
} from './client'

export const getOverview = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(async ({ data: env }) => {
    const [overview, serviceList, me] = await Promise.all([
      phloApi(`api/v1/overview?env=${env}`, overviewSchema),
      phloApi(`api/v1/services?env=${env}`, servicesSchema),
      phloApi(
        'api/v1/me',
        z.object({
          subject: z.string(),
          email: z.string().nullable(),
          principal_type: z.string(),
          roles: z.array(z.string()),
          permissions: z.record(z.string(), z.array(z.string())),
        }),
      ),
    ])
    if (overview.env !== env || serviceList.env !== env) {
      throw new Error('Phlo API returned data for a different environment.')
    }
    return { overview, services: serviceList.items, me }
  })
