/** Server functions for data shared by the app shell and overview. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { incidentSchema } from './incidents'
import { runSchema } from './pipelines'
import {
  environmentSchema,
  overviewSchema,
  phloApi,
  servicesSchema,
} from './client'

const incidentsPageSchema = z.object({
  env: environmentSchema,
  items: z.array(incidentSchema),
  next_cursor: z.string().nullable(),
})

export const getShell = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(async ({ data: env }) => {
    const [overview, serviceList, me, incidents] = await Promise.all([
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
      phloApi(`api/v1/incidents?env=${env}&limit=100`, incidentsPageSchema, {
        env,
      }),
    ])
    if (
      overview.env !== env ||
      serviceList.env !== env ||
      incidents.env !== env
    ) {
      throw new Error('Phlo API returned data for a different environment.')
    }
    return {
      overview,
      services: serviceList.items,
      environmentBinding: {
        dagsterLocation: serviceList.dagster_location ?? null,
        nessieRef: serviceList.nessie_ref ?? null,
      },
      me,
      incidents: incidents.items
        .filter((item) => item.status !== 'resolved')
        .slice(0, 5),
    }
  })

export const overviewRangeSchema = z.enum(['24h', '7d', '30d'])
const sourcesSchema = z.object({
  env: environmentSchema,
  items: z.array(
    z.object({
      id: z.string(),
      last_materialization_at: z.string().nullable(),
    }),
  ),
  next_cursor: z.string().nullable(),
})
const layersSchema = z.object({
  env: environmentSchema,
  items: z.array(
    z.object({
      group_name: z.string().nullable(),
      layer: z.enum(['bronze', 'silver', 'gold']).nullable().optional(),
      asset_count: z.number().int().nonnegative(),
      materialized_asset_count: z.number().int().nonnegative(),
      latest_materialization_at: z.string().nullable(),
      freshness_counts: z
        .object({
          fresh: z.number().int().nonnegative(),
          stale: z.number().int().nonnegative(),
          unknown: z.number().int().nonnegative(),
        })
        .nullable()
        .optional(),
    }),
  ),
  next_cursor: z.string().nullable(),
})
const runsPageSchema = z.object({
  env: environmentSchema,
  items: z.array(runSchema),
  next_cursor: z.string().nullable(),
})

export const getOverview = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({ env: environmentSchema, range: overviewRangeSchema }),
  )
  .handler(async ({ data: { env, range } }) => {
    const [overview, sources, layers, runs, incidents] = await Promise.all([
      phloApi(`api/v1/overview?env=${env}`, overviewSchema, { env }),
      phloApi(`api/v1/sources?env=${env}&limit=500`, sourcesSchema, { env }),
      phloApi(`api/v1/layers?env=${env}&limit=500`, layersSchema, { env }),
      phloApi(`api/v1/runs?env=${env}&limit=100`, runsPageSchema, { env }),
      phloApi(`api/v1/incidents?env=${env}&limit=500`, incidentsPageSchema, {
        env,
      }),
    ])
    return {
      overview,
      sources,
      layers,
      runs,
      incidents,
      range,
      observedAt: new Date().toISOString(),
    }
  })
