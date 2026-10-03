/** Validates and executes incident, follow-up, and subscription operations. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'
import { getAssetDetail } from './assets'
import { runSchema } from './pipelines'

const statusSchema = z.enum(['open', 'acknowledged', 'resolved'])
export const severitySchema = z.enum(['low', 'medium', 'high'])
export const incidentSchema = z.object({
  id: z.string().min(1),
  asset_id: z.string().min(1),
  kind: z.string().min(1),
  title: z.string().min(1),
  status: statusSchema,
  owner: z.string().nullable(),
  version: z.number().int().nonnegative(),
  created_at: z.string().datetime({ offset: true }),
  updated_at: z.string().datetime({ offset: true }),
  severity: severitySchema.default('medium'),
  asset_ids: z.array(z.string()).default([]),
  layers: z.array(z.string()).default([]),
  description: z.string().default(''),
  notify_qa: z.boolean().default(false),
  pause_downstream: z.boolean().default(false),
  effects: z
    .array(
      z.object({
        id: z.string(),
        kind: z.enum(['notification', 'pause']),
        status: z.enum(['pending', 'delivering', 'delivered', 'failed']),
        attempts: z.number().int(),
        error: z.string().nullable(),
      }),
    )
    .default([]),
})
const incidentPageSchema = z.object({
  env: environmentSchema,
  items: z.array(incidentSchema),
  next_cursor: z.string().nullable(),
})
const statsSchema = z.object({
  env: environmentSchema,
  counts: z.record(z.string(), z.number().int().nonnegative()),
})
const timelineSchema = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      actor: z.string(),
      kind: z.string(),
      payload: z.record(z.string(), z.json()),
      occurred_at: z.string().datetime({ offset: true }),
    }),
  ),
})
export const followUpSchema = z.object({
  id: z.string(),
  description: z.string(),
  due_at: z.string().datetime({ offset: true }).nullable(),
  completed_at: z.string().datetime({ offset: true }).nullable(),
})
const followUpsSchema = z.object({ items: z.array(followUpSchema) })
const subscriptionSchema = z.object({
  incident_id: z.string(),
  subscribed: z.boolean(),
})
const signatureSchema = z.object({ signature_id: z.string().min(1) })
const jsonText = <T>(schema: z.ZodType<T>) =>
  z.string().transform((text, context) => {
    try {
      return schema.parse(JSON.parse(text))
    } catch {
      context.addIssue({ code: 'custom', message: 'Invalid API response' })
      return z.NEVER
    }
  })

export type IncidentRecord = z.infer<typeof incidentSchema>
export type IncidentTimelineEvent = z.infer<
  typeof timelineSchema
>['items'][number]
export type IncidentFollowUp = z.infer<typeof followUpSchema>

export function incidentOperationKey(
  env: string,
  incidentId: string,
  action: string,
  intent: string,
) {
  const storageKey = `phlo:incident:${env}:${incidentId}:${action}:${intent}`
  const existing = sessionStorage.getItem(storageKey)
  if (existing) return existing
  const key = crypto.randomUUID()
  sessionStorage.setItem(storageKey, key)
  return key
}

export function clearIncidentOperationKey(
  env: string,
  incidentId: string,
  action: string,
  intent: string,
) {
  sessionStorage.removeItem(
    `phlo:incident:${env}:${incidentId}:${action}:${intent}`,
  )
}

const detailInput = z.object({ env: environmentSchema, id: z.string().min(1) })

export const getIncidentList = createServerFn({ method: 'GET' })
  .inputValidator(environmentSchema)
  .handler(async ({ data: env }) => {
    const [page, stats] = await Promise.all([
      phloApi(`api/v1/incidents?env=${env}&limit=500`, incidentPageSchema, {
        env,
      }),
      phloApi(`api/v1/incidents/stats?env=${env}`, statsSchema, { env }),
    ])
    return {
      incidents: page.items,
      stats: stats.counts,
      truncated: page.next_cursor !== null,
    }
  })

export const getIncidentDetail = createServerFn({ method: 'GET' })
  .inputValidator(detailInput)
  .handler(async ({ data: { env, id } }) => {
    const encoded = encodeURIComponent(id)
    const [incident, timeline, followUps] = await Promise.all([
      phloApi(`api/v1/incidents/${encoded}?env=${env}`, incidentSchema),
      phloApi(
        `api/v1/incidents/${encoded}/timeline?env=${env}`,
        timelineSchema,
      ),
      phloApi(
        `api/v1/incidents/${encoded}/follow-ups?env=${env}`,
        followUpsSchema,
      ),
    ])
    const runs = await phloApi(
      `api/v1/runs?env=${env}&limit=100`,
      z.object({
        env: environmentSchema,
        items: z.array(runSchema),
        next_cursor: z.string().nullable(),
      }),
      { env },
    )
      .then((page) => ({
        items: page.items.filter(
          (run) =>
            run.selected_assets.some((key) =>
              (incident.asset_ids.length
                ? incident.asset_ids
                : [incident.asset_id]
              ).some(
                (asset) => asset === key.join('/') || asset === key.join('.'),
              ),
            ) ||
            timeline.items.some((event) => {
              const evidence = event.payload.evidence
              return (
                typeof evidence === 'object' &&
                evidence !== null &&
                !Array.isArray(evidence) &&
                (evidence.run_id === run.run_id ||
                  evidence.successful_run_id === run.run_id)
              )
            }),
        ),
        truncated: page.next_cursor !== null,
        error: null,
      }))
      .catch((error: unknown) => ({
        items: [],
        truncated: false,
        error:
          error instanceof Error
            ? error.message
            : 'Run history is unavailable.',
      }))
    const tab = incident.kind.toLowerCase().includes('schema')
      ? 'schema'
      : /audit|check|quality/.test(incident.kind.toLowerCase())
        ? 'audits'
        : 'overview'
    const investigation = await getAssetDetail({
      data: { env, id: incident.asset_id, tab },
    }).catch((error: unknown) => ({
      kind: 'unavailable' as const,
      message:
        error instanceof Error
          ? error.message
          : 'Asset investigation is unavailable.',
    }))
    return {
      incident,
      timeline: timeline.items,
      followUps: followUps.items,
      runs,
      investigation,
    }
  })

export const createIncident = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      idempotency_key: z.string().min(1),
      asset_id: z.string().min(1).max(512),
      kind: z.string().min(1).max(100),
      title: z.string().min(1).max(500),
      evidence_id: z.string().min(1).max(512),
      evidence: z.record(z.string(), z.json()),
      severity: severitySchema,
      owner: z.string().max(512).nullable(),
      asset_ids: z.array(z.string().min(1).max(512)).min(1).max(100),
      description: z.string().max(10000),
      notify_qa: z.boolean(),
      pause_downstream: z.boolean(),
    }),
  )
  .handler(({ data }) =>
    phloApi(`api/v1/incidents?env=${data.env}`, jsonText(incidentSchema), {
      method: 'POST',
      env: data.env,
      idempotencyKey: data.idempotency_key,
      responseType: 'text',
      body: {
        asset_id: data.asset_id,
        kind: data.kind,
        title: data.title,
        evidence_id: data.evidence_id,
        evidence: data.evidence,
        severity: data.severity,
        owner: data.owner,
        asset_ids: data.asset_ids,
        description: data.description,
        notify_qa: data.notify_qa,
        pause_downstream: data.pause_downstream,
      },
    }),
  )

async function patchIncident(
  data: z.infer<typeof updateInput>,
): Promise<IncidentRecord> {
  return phloApi(
    `api/v1/incidents/${encodeURIComponent(data.id)}?env=${data.env}`,
    incidentSchema,
    {
      method: 'PATCH',
      body: data.update,
      idempotencyKey: data.idempotency_key,
      headers: { 'if-match': String(data.version) },
    },
  )
}

const updateInput = z.object({
  env: environmentSchema,
  id: z.string().min(1),
  version: z.number().int().nonnegative(),
  idempotency_key: z.string().min(1),
  update: z
    .object({
      status: statusSchema.optional(),
      owner: z.string().max(512).nullable().optional(),
      comment: z.string().max(10_000).optional(),
      signature_id: z.string().min(1).max(100).optional(),
      severity: severitySchema.optional(),
      asset_ids: z.array(z.string().min(1).max(512)).min(1).max(100).optional(),
      description: z.string().max(10000).optional(),
      notify_qa: z.boolean().optional(),
      pause_downstream: z.boolean().optional(),
      query_id: z.string().min(1).max(100).optional(),
      retry_effects: z.boolean().optional(),
    })
    .refine((value) => Object.keys(value).length > 0),
})

export const updateIncident = createServerFn({ method: 'POST' })
  .inputValidator(updateInput)
  .handler(({ data }) => patchIncident(data))

export const setIncidentSubscription = createServerFn({ method: 'POST' })
  .inputValidator(
    detailInput.extend({
      subscribed: z.boolean(),
      idempotency_key: z.string().min(1),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `api/v1/incidents/${encodeURIComponent(data.id)}/subscriptions?env=${data.env}&subscribed=${data.subscribed}`,
      jsonText(subscriptionSchema),
      {
        method: 'PUT',
        env: data.env,
        idempotencyKey: data.idempotency_key,
        responseType: 'text',
        body: {},
      },
    ),
  )

export const createFollowUp = createServerFn({ method: 'POST' })
  .inputValidator(
    detailInput.extend({
      description: z.string().min(1).max(4000),
      due_at: z.string().datetime().nullable(),
      idempotency_key: z.string().min(1),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `api/v1/incidents/${encodeURIComponent(data.id)}/follow-ups?env=${data.env}`,
      jsonText(followUpSchema),
      {
        method: 'POST',
        env: data.env,
        idempotencyKey: data.idempotency_key,
        responseType: 'text',
        body: { description: data.description, due_at: data.due_at },
      },
    ),
  )

export const updateFollowUp = createServerFn({ method: 'POST' })
  .inputValidator(
    detailInput.extend({
      follow_up_id: z.string().min(1),
      completed: z.boolean(),
      idempotency_key: z.string().min(1),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `api/v1/incidents/${encodeURIComponent(data.id)}/follow-ups/${encodeURIComponent(data.follow_up_id)}?env=${data.env}`,
      jsonText(followUpSchema),
      {
        method: 'PATCH',
        env: data.env,
        idempotencyKey: data.idempotency_key,
        responseType: 'text',
        body: { completed: data.completed },
      },
    ),
  )

export const resolveIncident = createServerFn({ method: 'POST' })
  .inputValidator(
    detailInput.extend({
      version: z.number().int().nonnegative(),
      comment: z.string().trim().min(1).max(4000),
      idempotency_key: z.string().min(1),
    }),
  )
  .handler(async ({ data }) => {
    const signature = await phloApi(
      'api/v1/signatures',
      jsonText(signatureSchema),
      {
        method: 'POST',
        env: data.env,
        responseType: 'text',
        idempotencyKey: `signature:${data.idempotency_key}`,
        body: {
          action: 'incident.resolve',
          target_type: 'incident',
          target_id: `${data.env}:${data.id}`,
          target_version: String(data.version),
          meaning: 'approved',
          justification: data.comment,
        },
      },
    )
    return patchIncident({
      ...data,
      update: {
        status: 'resolved',
        comment: data.comment,
        signature_id: signature.signature_id,
      },
    })
  })
