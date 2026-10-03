/** Displays the signed details and payload of an audit record. */
import type { AuditRecord, AuditSignature } from '@/lib/data/api/admin'
import { Eyebrow } from '@/components/phlo/page'
import { Mono } from '@/components/phlo/status'

export function AuditDetail({
  record,
  signature,
}: {
  record: AuditRecord
  signature?: AuditSignature
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
      <SignatureEvidence event={event} signature={signature} />
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

function SignatureEvidence({
  event,
  signature,
}: {
  event: AuditRecord['event']
  signature?: AuditSignature
}) {
  return signature ? (
    <dl className="m-0 grid grid-cols-[96px_minmax(0,1fr)] gap-y-[9px] rounded-[10px] border border-border bg-card p-3.5">
      <Eyebrow className="col-span-2 pb-0.5">Signature manifest</Eyebrow>
      <dt className="text-[13px] text-muted-foreground">Signed by</dt>
      <dd className="m-0 text-[13px] break-all">{signature.signer_subject}</dd>
      <dt className="text-[13px] text-muted-foreground">Meaning</dt>
      <dd className="m-0 text-[13px]">{signature.meaning}</dd>
      <dt className="text-[13px] text-muted-foreground">Signed at</dt>
      <dd className="m-0 font-mono text-xs break-all">{signature.signed_at}</dd>
      <dt className="text-[13px] text-muted-foreground">Assurance</dt>
      <dd className="m-0 text-[13px]">{signature.authentication_assurance}</dd>
      <dt className="text-[13px] text-muted-foreground">Action</dt>
      <dd className="m-0 text-[13px] break-all">{signature.action}</dd>
      <dt className="text-[13px] text-muted-foreground">Record</dt>
      <dd className="m-0 font-mono text-xs break-all">
        {signature.target_type} · {signature.target_id}
      </dd>
      <dt className="text-[13px] text-muted-foreground">Revision</dt>
      <dd className="m-0 font-mono text-xs break-all">
        {signature.target_version}
      </dd>
      <dt className="text-[13px] text-muted-foreground">Consumed</dt>
      <dd className="m-0 text-[13px] break-all">
        {signature.consumed_at ?? 'Not consumed'}
      </dd>
      {signature.justification ? (
        <>
          <dt className="text-[13px] text-muted-foreground">Justification</dt>
          <dd className="m-0 text-[13px]">{signature.justification}</dd>
        </>
      ) : null}
      <dt className="text-[13px] text-muted-foreground">Signature</dt>
      <dd className="m-0 font-mono text-[11.5px] break-all">
        {signature.signature_hash}
      </dd>
    </dl>
  ) : (
    <div
      className={
        event.decision === 'deny'
          ? 'rounded-[10px] border border-bad-line bg-bad-wash px-3.5 py-3 text-[13px] text-bad-ink'
          : 'rounded-[10px] border border-border bg-card px-3.5 py-3 text-[13px] text-muted-foreground'
      }
    >
      {typeof event.attributes?.signature_id === 'string'
        ? 'Signature evidence is linked, but its manifest is not accessible to your account.'
        : event.decision === 'deny'
          ? 'This action was refused. No signature evidence is recorded.'
          : 'No signature evidence is recorded for this action.'}
    </div>
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
