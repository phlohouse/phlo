/** Defines the searchable audit-log route with verification and export controls. */
import * as React from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { DownloadIcon, ShieldAlertIcon, ShieldCheckIcon } from 'lucide-react'
import type { AuditRecord } from '@/lib/data/api/admin'
import { exportAuditLog, getAuditLog } from '@/lib/data/api/admin'
import { PageHeader } from '@/components/phlo/page'
import { AuditDetail } from '@/components/settings/audit-detail'
import { SettingsFrame } from '@/components/settings/frame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/settings/audit-log')({
  loader: () => getAuditLog(),
  head: () => ({ meta: [{ title: 'Audit log · phlo' }] }),
  component: AuditLogPage,
})

const rowGrid =
  'md:grid md:grid-cols-[80px_100px_minmax(0,1fr)_64px] md:items-center md:gap-x-3.5'

function AuditLogPage() {
  const data = Route.useLoaderData()
  const [selectedSequence, setSelectedSequence] = React.useState<number | null>(
    data.items[0]?.sequence_number ?? null,
  )
  const [actor, setActor] = React.useState('')
  const [action, setAction] = React.useState('')
  const [signedOnly, setSignedOnly] = React.useState(false)
  const signatures = new Map(
    data.signatures.map((signature) => [signature.signature_id, signature]),
  )
  const rows = data.items.filter((record) => {
    const signatureId =
      typeof record.event.attributes?.signature_id === 'string'
        ? record.event.attributes.signature_id
        : null
    return (
      (!actor || record.event.actor_subject === actor) &&
      (!action || record.event.action === action) &&
      (!signedOnly || signatureId !== null)
    )
  })
  const selected = data.items.find(
    (record) => record.sequence_number === selectedSequence,
  )

  async function downloadExport() {
    const body = await exportAuditLog()
    const url = URL.createObjectURL(
      new Blob([body], { type: 'application/x-ndjson' }),
    )
    const link = document.createElement('a')
    link.href = url
    link.download = 'audit-phlo-api.jsonl'
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <SettingsFrame
      header={
        <PageHeader
          crumbs={[{ label: 'Settings', to: '/settings' }]}
          title="Audit log"
          actions={
            <>
              <VerificationStatus verification={data.verification} />
              <Button
                variant="outline"
                className="h-10 lg:h-8"
                onClick={downloadExport}
              >
                <DownloadIcon /> Export JSONL
              </Button>
            </>
          }
        />
      }
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto xl:flex-row xl:overflow-hidden">
        <section
          aria-label="Events"
          className="flex min-w-0 flex-1 flex-col xl:overflow-y-auto"
        >
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line p-3 lg:px-5">
            <Select
              aria-label="Filter by actor"
              value={actor}
              onValueChange={setActor}
              options={[
                { value: '', label: 'All actors' },
                ...[
                  ...new Set(
                    data.items.map((record) => record.event.actor_subject),
                  ),
                ].map((value) => ({ value, label: value })),
              ]}
              className="w-44"
            />
            <Select
              aria-label="Filter by action"
              value={action}
              onValueChange={setAction}
              options={[
                { value: '', label: 'All actions' },
                ...[
                  ...new Set(data.items.map((record) => record.event.action)),
                ].map((value) => ({ value, label: value })),
              ]}
              className="w-52"
            />
            <Button
              variant={signedOnly ? 'default' : 'outline'}
              size="sm"
              aria-pressed={signedOnly}
              onClick={() => setSignedOnly((value) => !value)}
            >
              Signed only
            </Button>
            <span
              className="ml-auto text-[13px] text-muted-foreground"
              aria-live="polite"
            >
              {rows.length} shown ·{' '}
              {data.verification.total_records.toLocaleString('en-GB')} total
            </span>
          </div>
          <div className="flex flex-col">
            <div
              aria-hidden
              className={cn(
                rowGrid,
                'hidden min-h-9 bg-raised px-5 text-xs text-muted-foreground',
              )}
            >
              <span>When</span>
              <span>Who</span>
              <span>What</span>
              <span>Decision</span>
            </div>
            {rows.map((record) => (
              <EventRow
                key={record.sequence_number}
                record={record}
                selected={record.sequence_number === selectedSequence}
                onSelect={() => setSelectedSequence(record.sequence_number)}
              />
            ))}
            {rows.length === 0 ? (
              <p className="m-0 px-5 py-6 text-[13.5px] text-muted-foreground">
                {data.items.length === 0
                  ? 'No audit events have been recorded.'
                  : 'No events match these filters.'}
              </p>
            ) : null}
            {data.next_after !== null || data.scan_truncated ? (
              <p className="m-0 px-5 py-3 text-xs text-muted-foreground">
                Showing the first {data.items.length} records. Export includes
                up to 5,000 records.
              </p>
            ) : null}
          </div>
        </section>
        <aside
          aria-label="Selected event"
          aria-live="polite"
          className="flex shrink-0 flex-col gap-[18px] border-t border-line bg-raised px-4 py-5 xl:w-[360px] xl:overflow-y-auto xl:border-t-0 xl:border-l xl:px-[22px]"
        >
          {selected ? (
            <AuditDetail
              record={selected}
              signature={
                typeof selected.event.attributes?.signature_id === 'string'
                  ? signatures.get(selected.event.attributes.signature_id)
                  : undefined
              }
            />
          ) : (
            <p className="m-0 text-[13.5px] text-muted-foreground">
              Select an event to inspect it.
            </p>
          )}
        </aside>
      </div>
    </SettingsFrame>
  )
}

function VerificationStatus({
  verification,
}: {
  verification: {
    valid: boolean
    total_records: number
    first_invalid_sequence: number | null
    error_message: string | null
  }
}) {
  return verification.valid ? (
    <span className="flex items-center gap-1.5 text-[13px] text-ok-text">
      <ShieldCheckIcon className="size-3.5" aria-hidden /> Hash chain verified
      now
    </span>
  ) : (
    <span className="flex items-center gap-1.5 text-[13px] text-bad-ink">
      <ShieldAlertIcon className="size-3.5" aria-hidden /> Verification failed
      at #{verification.first_invalid_sequence ?? 'unknown'}
      {verification.error_message ? `: ${verification.error_message}` : ''}
    </span>
  )
}

function EventRow({
  record,
  selected,
  onSelect,
}: {
  record: AuditRecord
  selected: boolean
  onSelect: () => void
}) {
  const date = new Date(record.sealed_at)
  return (
    <div
      className={cn(
        'border-b border-line-soft',
        selected ? 'bg-primary-soft' : 'hover:bg-raised',
      )}
    >
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={cn(
          rowGrid,
          'grid w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] gap-3 px-4 py-2.5 text-left text-foreground md:min-h-[54px] md:px-5 md:py-1.5',
        )}
      >
        <span className="hidden flex-col md:flex">
          <span className="text-[13.5px]">
            {formatTime(date, record.sealed_at)}
          </span>
          <span className="text-xs text-muted-foreground">
            {formatDay(date)}
          </span>
        </span>
        <span className="hidden min-w-0 flex-col md:flex">
          <span className="truncate text-[13.5px]">
            {record.event.actor_subject}
          </span>
          <span className="text-xs text-muted-foreground">
            {record.event.actor_type ?? 'unknown'}
          </span>
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="break-all text-[13.5px]">{record.event.action}</span>
          <span className="break-all font-mono text-xs text-muted-foreground">
            {record.event.resource_type ?? 'resource'} ·{' '}
            {record.event.resource_id ?? '—'}
          </span>
          <span className="break-all text-xs text-muted-foreground md:hidden">
            {formatDay(date)} {formatTime(date, record.sealed_at)} ·{' '}
            {record.event.actor_subject}
          </span>
        </span>
        <span>
          <Badge
            variant={
              record.event.decision === 'deny' ||
              record.event.outcome === 'failure'
                ? 'bad'
                : record.event.decision === 'allow' ||
                    record.event.outcome === 'success'
                  ? 'ok'
                  : 'neutral'
            }
          >
            {record.event.decision ?? record.event.outcome ?? 'recorded'}
          </Badge>
        </span>
      </button>
    </div>
  )
}

function formatTime(date: Date, fallback: string) {
  return Number.isNaN(date.valueOf())
    ? fallback
    : date.toLocaleTimeString('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'UTC',
      })
}
function formatDay(date: Date) {
  return Number.isNaN(date.valueOf())
    ? 'Unknown date'
    : date.toLocaleDateString('en-GB', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        timeZone: 'UTC',
      })
}
