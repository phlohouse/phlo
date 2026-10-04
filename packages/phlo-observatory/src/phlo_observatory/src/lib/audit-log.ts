/** Derives signature evidence and exports the displayed audit-log page. */
import type { AuditRecord, AuditSignature } from '@/lib/data/api/admin'

export function signatureEvidence(
  record: AuditRecord,
  signatures: Array<AuditSignature>,
) {
  const id = record.event.attributes?.signature_id
  const signature =
    typeof id === 'string'
      ? signatures.find((item) => item.signature_id === id)
      : undefined
  return {
    signature,
    label: signature
      ? signature.meaning
      : typeof id === 'string'
        ? 'Linked'
        : '',
  }
}

/** Export the displayed, filtered page, with the reference CSV column contract. */
export function auditLogCsv(
  rows: Array<AuditRecord>,
  signatures: Array<AuditSignature>,
) {
  const escape = (value: string) =>
    `"${(/^[=+\-@\t\r]/.test(value) ? "'" + value : value).replace(/"/g, '""')}"`
  const lines = ['id,day,time,actor,kind,action,object,signature']
  for (const record of rows) {
    const date = new Date(record.sealed_at)
    lines.push(
      [
        String(record.sequence_number),
        date.toISOString().slice(0, 10),
        date.toISOString().slice(11, 19),
        record.event.actor_subject,
        record.event.actor_type ?? 'unknown',
        record.event.action,
        record.event.resource_id ?? '',
        signatureEvidence(record, signatures).label,
      ]
        .map(escape)
        .join(','),
    )
  }
  return lines.join('\n') + '\n'
}
