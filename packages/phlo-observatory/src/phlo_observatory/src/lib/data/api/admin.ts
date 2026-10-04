/** Validates and executes administration, membership, and audit API operations. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { phloApi } from './client'

export const adminRoleSchema = z.enum([
  'admin',
  'operator',
  'developer',
  'analyst',
  'viewer',
  'service',
])
const memberSchema = z.object({
  subject: z.string(),
  email: z.string().nullable(),
  principal_type: z.string(),
  roles: z.array(adminRoleSchema),
  active: z.boolean(),
  version: z.number().int().nonnegative(),
  created_at: z.string(),
  updated_at: z.string(),
})
const invitationSchema = z.object({
  invitation_id: z.string(),
  email: z.string(),
  roles: z.array(adminRoleSchema),
  status: z.string(),
  invited_by: z.string(),
  expires_at: z.string(),
  created_at: z.string(),
})
const serviceAccountSchema = z.object({
  subject: z.string(),
  name: z.string(),
  roles: z.array(adminRoleSchema),
  active: z.boolean(),
  version: z.number().int().nonnegative(),
  created_at: z.string(),
})
const signatureSchema = z.object({
  signature_id: z.string(),
  signer_subject: z.string(),
  meaning: z.string(),
  action: z.string(),
  target_type: z.string(),
  target_id: z.string(),
  target_version: z.string(),
  justification: z.string().nullable(),
  signed_at: z.string(),
  authentication_assurance: z.string(),
  signature_hash: z.string(),
  consumed_at: z.string().nullable(),
})
const auditEventSchema = z.object({
  event_type: z.string(),
  surface: z.string(),
  actor_subject: z.string(),
  actor_type: z.string().nullable().optional(),
  actor_roles: z.array(z.string()).optional(),
  authentication_source: z.string().nullable().optional(),
  action: z.string(),
  resource_type: z.string().nullable().optional(),
  resource_id: z.string().nullable().optional(),
  decision: z.string().nullable().optional(),
  reason_code: z.string().nullable().optional(),
  outcome: z.string().nullable().optional(),
  attributes: z.record(z.string(), z.json()).optional(),
})
const auditRecordSchema = z.object({
  sequence_number: z.number().int().positive(),
  sealed_at: z.string(),
  previous_hash: z.string(),
  record_hash: z.string(),
  event: auditEventSchema,
})
const auditPageSchema = z.object({
  surface: z.string(),
  items: z.array(auditRecordSchema),
  next_after: z.number().int().nullable(),
  scan_truncated: z.boolean(),
})
const verificationSchema = z.object({
  surface: z.string(),
  valid: z.boolean(),
  total_records: z.number().int().nonnegative(),
  first_invalid_sequence: z.number().int().nullable(),
  error_message: z.string().nullable(),
})

export type AdminRole = z.infer<typeof adminRoleSchema>
export type AdminMember = z.infer<typeof memberSchema>
export type AdminInvitation = z.infer<typeof invitationSchema>
export type AdminServiceAccount = z.infer<typeof serviceAccountSchema>
export type AuditRecord = z.infer<typeof auditRecordSchema>
export type AuditSignature = z.infer<typeof signatureSchema>

const page = <T extends z.ZodType>(item: T) =>
  z.object({ items: z.array(item) })
const actionInput = z.object({
  justification: z.string().trim().min(1).max(4000),
  confirmed: z.literal(true),
})

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value)
  if (typeof value === 'number') {
    if (!Number.isFinite(value))
      throw new Error('Canonical payload numbers must be finite.')
    return JSON.stringify(value)
  }
  if (typeof value === 'string') {
    return JSON.stringify(value).replace(
      /[\u0080-\uffff]/g,
      (character) =>
        `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`,
    )
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (typeof value === 'object') {
    return `{${Object.entries(value)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => `${canonicalJson(key)}:${canonicalJson(item)}`)
      .join(',')}}`
  }
  throw new Error('Canonical payload contains an unsupported value.')
}

export async function payloadVersion(
  expectedVersion: number,
  values: Record<string, unknown>,
) {
  const canonical = canonicalJson(values)
  const hash = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(canonical),
  )
  return `${expectedVersion}:${Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('')}`
}

function normalizeRoles(roles: Array<AdminRole>) {
  return [...new Set(roles)].sort()
}

function adminError(error: unknown): never {
  const message = error instanceof Error ? error.message : ''
  if (message.includes('(403)'))
    throw new Error(
      'Permission denied. An admin session with recent MFA is required.',
    )
  if (message.includes('(401)'))
    throw new Error('Your authenticated session has expired. Sign in again.')
  if (message.includes('(409)'))
    throw new Error(
      'This record changed or the approval could not be used. Refresh and try again.',
    )
  throw error
}

async function sign(input: {
  action: string
  targetType: string
  targetId: string
  targetVersion: string
  justification: string
  operationId: string
}) {
  return phloApi('api/v1/signatures', signatureSchema, {
    method: 'POST',
    idempotencyKey: `signature:${input.operationId}`,
    body: {
      action: input.action,
      target_type: input.targetType,
      target_id: input.targetId,
      target_version: input.targetVersion,
      meaning: 'approved',
      justification: input.justification,
    },
  })
}

export const getAdminIdentity = createServerFn({ method: 'GET' }).handler(
  async () => {
    const [members, invitations, serviceAccounts] = await Promise.all([
      phloApi('api/v1/admin/members', page(memberSchema)),
      phloApi('api/v1/admin/invitations', page(invitationSchema)),
      phloApi('api/v1/admin/service-accounts', page(serviceAccountSchema)),
    ])
    return {
      members: members.items,
      invitations: invitations.items,
      serviceAccounts: serviceAccounts.items,
    }
  },
)

const roleChangeInput = actionInput.extend({
  subject: z.string().min(1),
  expectedVersion: z.number().int().nonnegative(),
  roles: z.array(adminRoleSchema).min(1),
  email: z.string().nullable(),
  principalType: z.enum(['user', 'service', 'platform']),
  active: z.boolean(),
  operationId: z.string().uuid(),
})
export const changeMemberRoles = createServerFn({ method: 'POST' })
  .inputValidator(roleChangeInput)
  .handler(async ({ data }) => {
    const roles = normalizeRoles(data.roles)
    const values = {
      active: data.active,
      email: data.email?.trim().toLowerCase() ?? null,
      principal_type: data.principalType,
      roles,
    }
    try {
      const signature = await sign({
        action: 'admin.member.roles.change',
        targetType: 'member',
        targetId: data.subject,
        targetVersion: await payloadVersion(data.expectedVersion, values),
        justification: data.justification,
        operationId: data.operationId,
      })
      return await phloApi(
        `api/v1/admin/members/${encodeURIComponent(data.subject)}/roles`,
        memberSchema,
        {
          method: 'PATCH',
          idempotencyKey: data.operationId,
          body: {
            expected_version: data.expectedVersion,
            roles,
            email: data.email,
            principal_type: data.principalType,
            active: data.active,
            signature_id: signature.signature_id,
          },
        },
      )
    } catch (error) {
      return adminError(error)
    }
  })

const invitationInput = actionInput.extend({
  email: z.string().trim().email(),
  roles: z.array(adminRoleSchema).min(1),
  operationId: z.string().uuid(),
})
export const createInvitation = createServerFn({ method: 'POST' })
  .inputValidator(invitationInput)
  .handler(async ({ data }) => {
    const email = data.email.toLowerCase()
    const roles = normalizeRoles(data.roles)
    try {
      const signature = await sign({
        action: 'admin.invitation.create',
        targetType: 'invitation',
        targetId: email,
        targetVersion: await payloadVersion(0, { email, roles }),
        justification: data.justification,
        operationId: data.operationId,
      })
      return await phloApi(
        'api/v1/admin/invitations',
        invitationSchema.extend({ token: z.string() }),
        {
          method: 'POST',
          idempotencyKey: data.operationId,
          body: { email, roles, signature_id: signature.signature_id },
        },
      )
    } catch (error) {
      return adminError(error)
    }
  })

const serviceCreateInput = actionInput.extend({
  name: z.string().trim().min(1).max(120),
  roles: z.array(adminRoleSchema).min(1),
  operationId: z.string().uuid(),
})
export const createServiceAccount = createServerFn({ method: 'POST' })
  .inputValidator(serviceCreateInput)
  .handler(async ({ data }) => {
    const roles = normalizeRoles(data.roles)
    try {
      const signature = await sign({
        action: 'admin.service_account.create',
        targetType: 'service_account',
        targetId: data.name,
        targetVersion: await payloadVersion(0, { name: data.name, roles }),
        justification: data.justification,
        operationId: data.operationId,
      })
      return await phloApi(
        'api/v1/admin/service-accounts',
        z.object({ account: serviceAccountSchema, token: z.string() }),
        {
          method: 'POST',
          idempotencyKey: data.operationId,
          body: {
            name: data.name,
            roles,
            signature_id: signature.signature_id,
          },
        },
      )
    } catch (error) {
      return adminError(error)
    }
  })

const serviceRevokeInput = actionInput.extend({
  subject: z.string().min(1),
  expectedVersion: z.number().int().positive(),
  operationId: z.string().uuid(),
})
export const revokeServiceAccount = createServerFn({ method: 'POST' })
  .inputValidator(serviceRevokeInput)
  .handler(async ({ data }) => {
    try {
      const signature = await sign({
        action: 'admin.service_account.revoke',
        targetType: 'service_account',
        targetId: data.subject,
        targetVersion: String(data.expectedVersion),
        justification: data.justification,
        operationId: data.operationId,
      })
      return await phloApi(
        `api/v1/admin/service-accounts/${encodeURIComponent(data.subject)}`,
        serviceAccountSchema,
        {
          method: 'DELETE',
          idempotencyKey: data.operationId,
          headers: {
            'expected-version': String(data.expectedVersion),
            'signature-id': signature.signature_id,
          },
        },
      )
    } catch (error) {
      return adminError(error)
    }
  })

const auditFilters = z.object({
  since: z.iso.datetime({ offset: true }).optional(),
  until: z.iso.datetime({ offset: true }).optional(),
  actor_subject: z.string().min(1).optional(),
  action: z.string().min(1).optional(),
  signed_only: z.boolean().optional(),
  after: z.number().int().nonnegative().optional(),
})
function auditQuery(data: z.infer<typeof auditFilters>) {
  const query = new URLSearchParams({ surface: 'phlo-api' })
  for (const [key, value] of Object.entries(data)) {
    if (value !== undefined) query.set(key, String(value))
  }
  return query
}

export const getAuditLog = createServerFn({ method: 'GET' })
  .inputValidator(auditFilters)
  .handler(async ({ data }) => {
    const query = auditQuery(data)
    query.set('limit', '500')
    const [records, verification, signatures] = await Promise.all([
      phloApi(`api/v1/admin/audit/records?${query}`, auditPageSchema),
      phloApi('api/v1/admin/audit/verify?surface=phlo-api', verificationSchema),
      phloApi('api/v1/signatures', page(signatureSchema)),
    ])
    return { ...records, verification, signatures: signatures.items }
  })

export const exportAuditLog = createServerFn({ method: 'GET' })
  .inputValidator(auditFilters)
  .handler(({ data }) => {
    const query = auditQuery(data)
    query.set('limit', '5000')
    return phloApi(`api/v1/admin/audit/export?${query}`, z.string(), {
      responseType: 'text',
    })
  })
