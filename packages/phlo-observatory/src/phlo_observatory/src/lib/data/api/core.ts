/** Server functions for data shared by the app shell and overview. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { incidentSchema } from './incidents'
import { runSchema } from './pipelines'
import {
  environmentSchema,
  optionalRead,
  overviewSchema,
  phloApi,
  servicesSchema,
} from './client'

const incidentsPageSchema = z.object({
  env: environmentSchema,
  items: z.array(incidentSchema),
  next_cursor: z.string().nullable(),
})

export const getEnvironments = createServerFn({ method: 'GET' })
  .inputValidator(z.string())
  .handler(async ({ data: selected }) => {
    const [discovery, me] = await Promise.all([
      phloApi(
        'api/v1/environments',
        z.object({
          items: z
            .array(
              z.object({
                env: environmentSchema,
                status: z.enum(['available', 'unavailable']),
              }),
            )
            .min(1),
          default_environment: environmentSchema.nullable().optional(),
        }),
      ),
      phloApi(
        'api/v1/me',
        z.object({
          permissions: z.record(environmentSchema, z.array(z.string())),
        }),
      ),
    ])
    if (
      Object.hasOwn(me.permissions, selected) &&
      !discovery.items.some((item) => item.env === selected)
    ) {
      throw new Error(
        'Phlo API access denied (403). This environment is configured but not permitted.',
      )
    }
    const fallback =
      discovery.items.find(
        (item) => item.env === discovery.default_environment,
      ) ?? discovery.items[0]
    if (!fallback) throw new Error('No authorized environment is configured.')
    return {
      ...discovery,
      selected: discovery.items.some((item) => item.env === selected)
        ? selected
        : fallback.env,
    }
  })

export const getShell = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(async ({ data: env }) => {
    const [overview, serviceList, me, incidents] = await Promise.all([
      optionalRead(
        phloApi(`api/v1/overview?env=${env}`, overviewSchema, { env }),
      ),
      optionalRead(
        phloApi(`api/v1/services?env=${env}`, servicesSchema, { env }),
      ),
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
      optionalRead(
        phloApi(`api/v1/incidents?env=${env}&limit=100`, incidentsPageSchema, {
          env,
        }),
      ),
    ])
    return {
      env,
      overview: overview.state === 'available' ? overview.data : null,
      services:
        serviceList.state === 'available' ? serviceList.data.items : null,
      panelErrors: [overview, serviceList, incidents].flatMap(
        (result, index) =>
          result.state === 'unavailable'
            ? [
                {
                  panel: ['Overview', 'Services', 'Incidents'][index],
                  message: result.error,
                },
              ]
            : [],
      ),
      environmentBinding: {
        dagsterLocation:
          serviceList.state === 'available'
            ? (serviceList.data.dagster_location ?? null)
            : null,
        nessieRef:
          serviceList.state === 'available'
            ? (serviceList.data.nessie_ref ?? null)
            : null,
      },
      me,
      incidents:
        incidents.state === 'available'
          ? incidents.data.items
              .filter((item) => item.status !== 'resolved')
              .slice(0, 5)
          : null,
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
      optionalRead(
        phloApi(`api/v1/overview?env=${env}`, overviewSchema, { env }),
      ),
      optionalRead(
        phloApi(`api/v1/sources?env=${env}&limit=500`, sourcesSchema, { env }),
      ),
      optionalRead(
        phloApi(`api/v1/layers?env=${env}&limit=500`, layersSchema, { env }),
      ),
      optionalRead(
        phloApi(`api/v1/runs?env=${env}&limit=100`, runsPageSchema, { env }),
      ),
      optionalRead(
        phloApi(`api/v1/incidents?env=${env}&limit=500`, incidentsPageSchema, {
          env,
        }),
      ),
    ])
    if (
      overview.state !== 'available' ||
      sources.state !== 'available' ||
      layers.state !== 'available' ||
      runs.state !== 'available' ||
      incidents.state !== 'available'
    )
      return {
        state: 'degraded' as const,
        env,
        panels: { overview, sources, layers, runs, incidents },
      }
    return {
      state: 'available' as const,
      data: {
        overview: overview.data,
        sources: sources.data,
        layers: layers.data,
        runs: runs.data,
        incidents: incidents.data,
        range,
        observedAt: new Date().toISOString(),
      },
    }
  })
