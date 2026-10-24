/** Loads actor-scoped incident notifications and personal read receipts. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

const viewSchema = z.enum(['for_you', 'watching', 'all'])
const itemSchema = z.object({
  id: z.string(),
  incident_id: z.string(),
  title: z.string(),
  asset_id: z.string(),
  actor: z.string(),
  kind: z.string(),
  occurred_at: z.string(),
  read: z.boolean(),
})
const pageSchema = z.object({
  env: environmentSchema,
  view: viewSchema,
  items: z.array(itemSchema),
  unread: z.number().int().nonnegative(),
  truncated: z.boolean(),
  next_cursor: z.string().nullable().optional(),
})
export type NotificationItem = z.infer<typeof itemSchema>
export type NotificationPage = z.infer<typeof pageSchema>
export type NotificationView = z.infer<typeof viewSchema>
export const getNotifications = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      view: viewSchema.default('for_you'),
      after: z.string().optional(),
      unread_only: z.boolean().optional(),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `/api/v1/notifications?${new URLSearchParams({ env: data.env, view: data.view, limit: '100', ...(data.after ? { after: data.after } : {}), ...(data.unread_only ? { unread_only: 'true' } : {}) })}`,
      pageSchema,
      { env: data.env },
    ),
  )
export const markNotificationsRead = createServerFn({ method: 'POST' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      eventIds: z.array(z.string().min(1).max(128)).min(1).max(100),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `/api/v1/notifications/read-state?env=${data.env}`,
      z.object({ event_ids: z.array(z.string()) }),
      {
        env: data.env,
        method: 'PUT',
        headers: { 'x-phlo-intent': 'notifications.mark-read' },
        body: { event_ids: data.eventIds },
      },
    ),
  )
export const getIncidentSubscription = createServerFn({ method: 'GET' })
  .inputValidator(z.object({ env: environmentSchema, id: z.string().min(1) }))
  .handler(({ data }) =>
    phloApi(
      `/api/v1/incidents/${encodeURIComponent(data.id)}/subscriptions?env=${data.env}`,
      z.object({ incident_id: z.string(), subscribed: z.boolean() }),
      { env: data.env },
    ),
  )
