/** Displays the signed details and payload of an audit record. */
import type { AuditRecord } from '@/lib/data/api/admin'
import { Eyebrow } from '@/components/phlo/page'
import { Mono } from '@/components/phlo/status'

type Signature = {
  signature_id: string
  signer_subject: string
  meaning: string
  justification: string | null
  signed_at: string
  authentication_assurance: string
  signature_hash: string
}

export function AuditDetail({
  record,
  signature,
}: {
  record: AuditRecord
  signature?: Signature
}) {
  const event = record.event
  const actorOrReason = describeActorOrReason(record)
  return (
    <>
      <div className="flex flex-col gap-1.5">
        <Eyebrow>Event #{record.sequence_number}</Eyebrow>
        <h2 className="m-0 text-base font-medium">{event.action}</h2>
        <Mono className="truncate text-xs">
          {event.resource_type ?? 'resource'} · {event.resource_id ?? '—'}
        </Mono>
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="text-[13.5px] text-muted-foreground">
          {actorOrReason.label}
        </div>
        <div className="text-[13.5px] leading-normal">
          {actorOrReason.value}
        </div>
      </div>
      <dl className="m-0 grid grid-cols-[96px_minmax(0,1fr)] gap-y-2 text-[13px]">
        <dt className="text-muted-foreground">Decision</dt>
        <dd className="m-0">{event.decision ?? '—'}</dd>
        <dt className="text-muted-foreground">Outcome</dt>
        <dd className="m-0">{event.outcome ?? '—'}</dd>
      </dl>
      {signature ? (
        <dl className="m-0 grid grid-cols-[96px_minmax(0,1fr)] gap-y-[9px] rounded-[10px] border border-border bg-card p-3.5">
          <Eyebrow className="col-span-2 pb-0.5">Signature manifest</Eyebrow>
          <dt className="text-[13px] text-muted-foreground">Signed by</dt>
          <dd className="m-0 text-[13px] break-all">
            {signature.signer_subject}
          </dd>
          <dt className="text-[13px] text-muted-foreground">Meaning</dt>
          <dd className="m-0 text-[13px]">{signature.meaning}</dd>
          <dt className="text-[13px] text-muted-foreground">Signed at</dt>
          <dd className="m-0 font-mono text-xs break-all">
            {signature.signed_at}
          </dd>
          <dt className="text-[13px] text-muted-foreground">Assurance</dt>
          <dd className="m-0 text-[13px]">
            {signature.authentication_assurance}
          </dd>
          {signature.justification ? (
            <>
              <dt className="text-[13px] text-muted-foreground">
                Justification
              </dt>
              <dd className="m-0 text-[13px]">{signature.justification}</dd>
            </>
          ) : null}
          <dt className="text-[13px] text-muted-foreground">Signature</dt>
          <dd className="m-0 font-mono text-[11.5px] break-all">
            {signature.signature_hash}
          </dd>
        </dl>
      ) : (
        <div className="rounded-[10px] border border-border bg-card px-3.5 py-3 text-[13px] text-muted-foreground">
          No accessible signature is linked to this event.
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <div className="text-[13.5px] text-muted-foreground">
          Linked resource
        </div>
        <Mono className="truncate text-xs">
          {event.resource_type ?? 'resource'} · {event.resource_id ?? '—'}
        </Mono>
      </div>
      {event.attributes && Object.keys(event.attributes).length > 0 ? (
        <details className="text-[13px]">
          <summary className="cursor-pointer text-muted-foreground">
            Raw event attributes
          </summary>
          <pre className="m-0 overflow-x-auto rounded-[10px] border border-border bg-card p-3 text-[11px] whitespace-pre-wrap break-all">
            {JSON.stringify(event.attributes, null, 2)}
          </pre>
        </details>
      ) : null}
      <div className="flex flex-col gap-1 xl:mt-auto">
        <div className="text-[13.5px] text-muted-foreground">Record hash</div>
        <div className="font-mono text-[11.5px] break-all text-text-3">
          {record.record_hash}
        </div>
        <div className="font-mono text-[11.5px] break-all text-text-3">
          Previous: {record.previous_hash}
        </div>
      </div>
    </>
  )
}

function describeActorOrReason(record: AuditRecord) {
  const event = record.event
  return event.reason_code
    ? { label: 'Reason', value: event.reason_code }
    : {
        label: 'Actor',
        value: `${event.actor_subject} (${event.actor_type ?? 'unknown'}) · ${record.sealed_at}`,
      }
}
