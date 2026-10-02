/** Translates, validates, and persists Observatory administration settings. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { phloApi } from './client'

const settingValueSchema = z.union([
  z.string(),
  z.number().finite(),
  z.boolean(),
  z.null(),
])
const adminSettingsSchema = z.object({
  version: z.number().int().nonnegative(),
  values: z.record(z.string(), settingValueSchema),
})

export const settingsSchema = z.object({
  sla: z.object({ bronze: z.string(), silver: z.string(), gold: z.string() }),
  openIncidentOnBreach: z.boolean(),
  holdDownstream: z.boolean(),
  chat: z.string(),
  digest: z.string(),
  notifyOwners: z.boolean(),
  notifyConsumers: z.boolean(),
  fileSize: z.string(),
  expireDays: z.string(),
  orphan: z.string(),
  keepTagged: z.boolean(),
  compactNightly: z.boolean(),
  protectMain: z.boolean(),
  requireReason: z.boolean(),
  signTags: z.boolean(),
  secondReviewer: z.boolean(),
  retention: z.string(),
})
export type Settings = z.infer<typeof settingsSchema>

export const emptySettings: Settings = {
  sla: { bronze: '', silver: '', gold: '' },
  openIncidentOnBreach: false,
  holdDownstream: false,
  chat: '',
  digest: '',
  notifyOwners: false,
  notifyConsumers: false,
  fileSize: '',
  expireDays: '',
  orphan: '',
  keepTagged: false,
  compactNightly: false,
  protectMain: false,
  requireReason: false,
  signTags: false,
  secondReviewer: false,
  retention: '',
}

const prefix = 'observatory.settings.'
const keys = {
  bronze: `${prefix}freshness.bronze_minutes`,
  silver: `${prefix}freshness.silver_minutes`,
  gold: `${prefix}freshness.gold_minutes`,
  openIncidentOnBreach: `${prefix}freshness.open_incident_on_breach`,
  holdDownstream: `${prefix}freshness.hold_downstream`,
  chat: `${prefix}alerts.chat_channel`,
  digest: `${prefix}alerts.email_digest`,
  notifyOwners: `${prefix}alerts.notify_owners`,
  notifyConsumers: `${prefix}alerts.notify_consumers`,
  fileSize: `${prefix}maintenance.target_file_size_mb`,
  expireDays: `${prefix}maintenance.expire_snapshots_days`,
  orphan: `${prefix}maintenance.orphan_cleanup`,
  keepTagged: `${prefix}maintenance.keep_tagged_snapshots`,
  compactNightly: `${prefix}maintenance.compact_nightly`,
  protectMain: `${prefix}audit.protect_main`,
  requireReason: `${prefix}audit.require_merge_reason`,
  signTags: `${prefix}audit.sign_release_tags`,
  secondReviewer: `${prefix}audit.second_gold_reviewer`,
  retention: `${prefix}audit.retention_years`,
} as const

function decode(
  values: Record<string, string | number | boolean | null>,
): Settings {
  const text = (key: string) =>
    typeof values[key] === 'string' ? values[key] : ''
  const flag = (key: string) => values[key] === true
  return {
    sla: {
      bronze: text(keys.bronze),
      silver: text(keys.silver),
      gold: text(keys.gold),
    },
    openIncidentOnBreach: flag(keys.openIncidentOnBreach),
    holdDownstream: flag(keys.holdDownstream),
    chat: text(keys.chat),
    digest: text(keys.digest),
    notifyOwners: flag(keys.notifyOwners),
    notifyConsumers: flag(keys.notifyConsumers),
    fileSize: text(keys.fileSize),
    expireDays: text(keys.expireDays),
    orphan: text(keys.orphan),
    keepTagged: flag(keys.keepTagged),
    compactNightly: flag(keys.compactNightly),
    protectMain: flag(keys.protectMain),
    requireReason: flag(keys.requireReason),
    signTags: flag(keys.signTags),
    secondReviewer: flag(keys.secondReviewer),
    retention: text(keys.retention),
  }
}

function encode(settings: Settings): Record<string, string | boolean> {
  return {
    [keys.bronze]: settings.sla.bronze,
    [keys.silver]: settings.sla.silver,
    [keys.gold]: settings.sla.gold,
    [keys.openIncidentOnBreach]: settings.openIncidentOnBreach,
    [keys.holdDownstream]: settings.holdDownstream,
    [keys.chat]: settings.chat,
    [keys.digest]: settings.digest,
    [keys.notifyOwners]: settings.notifyOwners,
    [keys.notifyConsumers]: settings.notifyConsumers,
    [keys.fileSize]: settings.fileSize,
    [keys.expireDays]: settings.expireDays,
    [keys.orphan]: settings.orphan,
    [keys.keepTagged]: settings.keepTagged,
    [keys.compactNightly]: settings.compactNightly,
    [keys.protectMain]: settings.protectMain,
    [keys.requireReason]: settings.requireReason,
    [keys.signTags]: settings.signTags,
    [keys.secondReviewer]: settings.secondReviewer,
    [keys.retention]: settings.retention,
  }
}

function settingsError(error: unknown): never {
  const message = error instanceof Error ? error.message : ''
  if (message.includes('(403)'))
    throw new Error(
      'Permission denied. An admin session is required to manage settings.',
    )
  if (message.includes('(401)'))
    throw new Error('Your authenticated session has expired. Sign in again.')
  if (message.includes('(409)'))
    throw new Error(
      'Settings changed since this page loaded. Reload the saved settings before trying again.',
    )
  throw error
}

export const getSettings = createServerFn({ method: 'GET' }).handler(
  async () => {
    try {
      const result = await phloApi('api/v1/admin/settings', adminSettingsSchema)
      return { version: result.version, settings: decode(result.values) }
    } catch (error) {
      return settingsError(error)
    }
  },
)

const saveInputSchema = z.object({
  expectedVersion: z.number().int().nonnegative(),
  settings: settingsSchema,
  idempotencyKey: z.string().min(1),
})

export const saveSettings = createServerFn({ method: 'POST' })
  .inputValidator(saveInputSchema)
  .handler(async ({ data }) => {
    try {
      const current = await phloApi(
        'api/v1/admin/settings',
        adminSettingsSchema,
      )
      const result = await phloApi(
        'api/v1/admin/settings',
        adminSettingsSchema,
        {
          method: 'PUT',
          idempotencyKey: data.idempotencyKey,
          body: {
            expected_version: data.expectedVersion,
            values: { ...current.values, ...encode(data.settings) },
          },
        },
      )
      return { version: result.version, settings: decode(result.values) }
    } catch (error) {
      return settingsError(error)
    }
  })
