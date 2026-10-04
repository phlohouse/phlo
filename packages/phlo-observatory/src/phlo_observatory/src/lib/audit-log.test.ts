/** Tests signature evidence and reference-format audit exports. */
import { expect, it } from 'vitest'
import { auditLogCsv, signatureEvidence } from './audit-log'
import type { AuditRecord, AuditSignature } from '@/lib/data/api/admin'

const record: AuditRecord = {
  sequence_number: 27,
  sealed_at: '2026-10-01T01:17:29+01:00',
  previous_hash: 'prev',
  record_hash: 'hash',
  event: {
    surface: 'phlo-api',
    event_type: 'operation',
    actor_subject: '=Alice',
    actor_type: 'user',
    action: 'branch.merge',
    resource_id: 'orders,"review"',
    attributes: { signature_id: 's1' },
  },
}
const signature: AuditSignature = {
  signature_id: 's1',
  signer_subject: 'Alice',
  meaning: 'approved',
  action: 'branch.merge',
  target_type: 'branch',
  target_id: 'candidate',
  target_version: 'v4',
  signed_at: '2026-10-01T00:16:00Z',
  justification: 'Reviewed',
  authentication_assurance: 'mfa',
  signature_hash: 'signature-hash',
  consumed_at: '2026-10-01T00:17:29Z',
}

it('exports only displayed rows, with UTC dates, quoted fields and safe spreadsheet values', () => {
  expect(auditLogCsv([record], [signature])).toBe(
    'id,day,time,actor,kind,action,object,signature\n"27","2026-10-01","00:17:29","\'=Alice","user","branch.merge","orders,""review""","approved"\n',
  )
  expect(auditLogCsv([], [signature])).toBe(
    'id,day,time,actor,kind,action,object,signature\n',
  )
})

it('does not invent signature approval from an allow decision or inaccessible manifest', () => {
  expect(signatureEvidence(record, []).label).toBe('Linked')
  expect(signatureEvidence(record, [signature]).signature?.target_version).toBe(
    'v4',
  )
  expect(
    signatureEvidence(
      {
        ...record,
        event: { ...record.event, attributes: {}, decision: 'allow' },
      },
      [signature],
    ),
  ).toEqual({ label: '', signature: undefined })
})
