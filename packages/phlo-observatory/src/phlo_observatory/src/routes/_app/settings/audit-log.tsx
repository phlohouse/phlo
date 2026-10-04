/** Defines the searchable audit-log route with verification and export controls. */
import * as React from 'react'
import { createFileRoute } from '@tanstack/react-router'
import {
  ChevronDownIcon,
  DownloadIcon,
  PlusIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  XIcon,
} from 'lucide-react'
import { z } from 'zod'
import type { AuditRecord } from '@/lib/data/api/admin'
import { getAuditLog } from '@/lib/data/api/admin'
import { auditLogCsv, signatureEvidence } from '@/lib/audit-log'
import { PageHeader } from '@/components/phlo/page'
import { AuditDetail } from '@/components/settings/audit-detail'
import { SettingsFrame } from '@/components/settings/frame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/menu'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/settings/audit-log')({
  validateSearch: z.object({
    range: z.enum(['week', 'all', 'custom']).default('week'),
    from: z.iso.date().optional(),
    to: z.iso.date().optional(),
    actor: z.string().optional(),
    action: z.string().optional(),
    signed: z.boolean().default(false),
    after: z.number().int().nonnegative().optional(),
  }),
  loaderDeps: ({ search }) => ({
    range: search.range,
    from: search.from,
    to: search.to,
    actor: search.actor,
    action: search.action,
    signed: search.signed,
    after: search.after,
  }),
  loader: ({ deps }) => {
    const today = new Date()
    today.setUTCHours(0, 0, 0, 0)
    const since =
      deps.range === 'week'
        ? new Date(today.valueOf() - 6 * 86400000).toISOString()
        : deps.range === 'custom' && deps.from
          ? `${deps.from}T00:00:00Z`
          : undefined
    const until =
      deps.range === 'custom' && deps.to
        ? new Date(
            new Date(`${deps.to}T00:00:00Z`).valueOf() + 86400000,
          ).toISOString()
        : undefined
    return getAuditLog({
      data: {
        since,
        until,
        actor_subject: deps.actor,
        action: deps.action,
        signed_only: deps.signed,
        after: deps.after,
      },
    })
  },
  head: () => ({ meta: [{ title: 'Audit log · phlo' }] }),
  component: AuditLogPage,
})

const rowGrid =
  'md:grid md:grid-cols-[92px_132px_minmax(0,1fr)_86px] md:items-center md:gap-x-3.5'
const chip =
  'flex h-10 shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-[13px] whitespace-nowrap lg:h-[30px]'
const chipOff =
  'border-dashed border-skip-line bg-card text-text-2 hover:bg-soft'
const chipOn = 'border-foreground bg-foreground text-background'

function AuditLogPage() {
  const data = Route.useLoaderData()
  const [selectedSequence, setSelectedSequence] = React.useState<number | null>(
    data.items[0]?.sequence_number ?? null,
  )
  const navigate = Route.useNavigate()
  const rows = data.items
  const selected =
    rows.find((record) => record.sequence_number === selectedSequence) ??
    rows[0]

  function downloadExport() {
    const body = auditLogCsv(rows, data.signatures)
    const url = URL.createObjectURL(new Blob([body], { type: 'text/csv' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'phlo-audit-log.csv'
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
                <DownloadIcon /> Export
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
          <AuditLogFilters data={data} />
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
              <span>Signature</span>
            </div>
            {rows.map((record) => (
              <EventRow
                key={record.sequence_number}
                record={record}
                selected={record.sequence_number === selected?.sequence_number}
                signatureLabel={
                  signatureEvidence(record, data.signatures).label
                }
                onSelect={() => setSelectedSequence(record.sequence_number)}
              />
            ))}
            {rows.length === 0 ? (
              <p className="m-0 px-5 py-6 text-[13.5px] text-muted-foreground">
                No events match these filters.
              </p>
            ) : null}
            {data.next_after !== null || data.scan_truncated ? (
              <div className="flex flex-wrap items-center gap-3 px-5 py-3 text-xs text-muted-foreground">
                <span>
                  Export includes the {rows.length} displayed records.
                  {data.scan_truncated
                    ? ' Search scan reached its 5,000-record budget.'
                    : ''}
                </span>
                {data.next_after !== null ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      void navigate({
                        search: (previous) => ({
                          ...previous,
                          after: data.next_after ?? undefined,
                        }),
                      })
                    }
                  >
                    Next page
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>
        <aside
          aria-label="Selected event"
          aria-live="polite"
          className="flex shrink-0 flex-col gap-[18px] border-t border-line bg-raised px-4 py-5 xl:w-[340px] xl:overflow-y-auto xl:border-t-0 xl:border-l xl:px-[22px]"
        >
          {selected ? (
            <AuditDetail
              record={selected}
              signature={signatureEvidence(selected, data.signatures).signature}
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

function AuditLogFilters({
  data,
}: {
  data: {
    items: Array<AuditRecord>
    verification: { total_records: number }
  }
}) {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const [customDates, setCustomDates] = React.useState(false)
  const [dateError, setDateError] = React.useState('')
  return (
    <>
      <div className="flex shrink-0 items-center gap-2 border-b border-line py-3 pl-4 lg:px-5">
        <div
          role="group"
          aria-label="Filter events"
          className="flex min-w-0 flex-1 gap-2 overflow-x-auto pr-4 [scrollbar-width:none] lg:pr-0"
        >
          <button
            type="button"
            aria-pressed={search.range === 'week'}
            className={cn(chip, search.range === 'week' ? chipOn : chipOff)}
            onClick={() =>
              void navigate({
                search: (previous) => ({
                  ...previous,
                  range: previous.range === 'week' ? 'all' : 'week',
                  after: undefined,
                }),
              })
            }
          >
            Last 7 days
          </button>
          <button
            type="button"
            aria-expanded={customDates}
            className={cn(chip, search.range === 'custom' ? chipOn : chipOff)}
            onClick={() => setCustomDates((value) => !value)}
          >
            {search.range === 'custom'
              ? `${search.from ?? 'Start'} – ${search.to ?? 'Today'}`
              : 'Date range'}
          </button>
          <FilterChip
            label="Actor"
            value={search.actor ?? ''}
            options={[
              ...new Set(
                data.items.map((record) => record.event.actor_subject),
              ),
            ]}
            onChange={(value) =>
              void navigate({
                search: (previous) => ({
                  ...previous,
                  actor: value || undefined,
                  after: undefined,
                }),
              })
            }
          />
          <FilterChip
            label="Action"
            value={search.action ?? ''}
            options={[
              ...new Set(data.items.map((record) => record.event.action)),
            ]}
            onChange={(value) =>
              void navigate({
                search: (previous) => ({
                  ...previous,
                  action: value || undefined,
                  after: undefined,
                }),
              })
            }
          />
          <button
            type="button"
            className={cn(chip, search.signed ? chipOn : chipOff)}
            aria-pressed={search.signed}
            onClick={() =>
              void navigate({
                search: (previous) => ({
                  ...previous,
                  signed: !previous.signed,
                  after: undefined,
                }),
              })
            }
          >
            Signed only
          </button>
        </div>
        <span
          className="ml-auto hidden shrink-0 text-[13px] text-muted-foreground sm:inline"
          aria-live="polite"
        >
          {data.items.length} shown ·{' '}
          {data.verification.total_records.toLocaleString('en-GB')} total
        </span>
      </div>
      {customDates ? (
        <form
          className="flex flex-wrap items-end gap-3 border-b border-line px-4 py-3"
          onSubmit={(event) => {
            event.preventDefault()
            const fields = new FormData(event.currentTarget)
            const from = String(fields.get('from') ?? '')
            const to = String(fields.get('to') ?? '')
            if (from && to && from > to) {
              setDateError('From must not be later than Through.')
              return
            }
            setDateError('')
            void navigate({
              search: (previous) => ({
                ...previous,
                range: 'custom',
                from: from || undefined,
                to: to || undefined,
                after: undefined,
              }),
            })
            setCustomDates(false)
          }}
        >
          <label className="flex flex-col gap-1 text-xs">
            From (UTC)
            <input
              name="from"
              type="date"
              defaultValue={search.from}
              className="h-9 rounded-md border border-input bg-card px-2"
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            Through (UTC)
            <input
              name="to"
              type="date"
              defaultValue={search.to}
              className="h-9 rounded-md border border-input bg-card px-2"
              required
            />
          </label>
          <Button type="submit" variant="outline">
            Apply dates
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              void navigate({
                search: (previous) => ({
                  ...previous,
                  range: 'all',
                  from: undefined,
                  to: undefined,
                  after: undefined,
                }),
              })
              setCustomDates(false)
            }}
          >
            Clear dates
          </Button>
          {dateError ? (
            <p role="alert" className="m-0 w-full text-xs text-bad-text">
              {dateError}
            </p>
          ) : null}
        </form>
      ) : null}
    </>
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
  signatureLabel,
  onSelect,
}: {
  record: AuditRecord
  selected: boolean
  signatureLabel: string
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
          <span className="truncate text-[13.5px]" title={record.event.action}>
            {record.event.action}
          </span>
          <span className="truncate font-mono text-xs text-muted-foreground">
            {record.event.resource_type ?? 'resource'} ·{' '}
            {record.event.resource_id ?? '—'}
          </span>
          <span className="truncate text-xs text-muted-foreground md:hidden">
            {formatDay(date)} {formatTime(date, record.sealed_at)} ·{' '}
            {record.event.actor_subject}
          </span>
        </span>
        <span>
          {signatureLabel ? (
            <Badge variant={signatureLabel === 'Linked' ? 'neutral' : 'ok'}>
              {signatureLabel}
            </Badge>
          ) : record.event.decision === 'deny' ? (
            <Badge variant="bad">Refused</Badge>
          ) : null}
        </span>
      </button>
    </div>
  )
}

function FilterChip({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: Array<string>
  onChange: (value: string) => void
}) {
  if (value)
    return (
      <span className={cn(chip, chipOn, 'cursor-default gap-1 pr-1')}>
        {label}: {value}
        <button
          type="button"
          aria-label={`Clear ${label.toLowerCase()} filter`}
          onClick={() => onChange('')}
          className="flex size-7 cursor-pointer items-center justify-center rounded-full hover:bg-background/20 lg:size-5"
        >
          <XIcon className="size-3" />
        </button>
      </span>
    )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={cn(chip, chipOff)}>
        <PlusIcon className="size-3" aria-hidden /> {label}
        <ChevronDownIcon className="size-3 text-muted-foreground" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {options.map((option) => (
          <DropdownMenuItem key={option} onClick={() => onChange(option)}>
            {option}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
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
