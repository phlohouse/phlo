/** Filters and renders grouped incident records. */
import { Link } from '@tanstack/react-router'
import { CheckIcon, ChevronRightIcon, PlusIcon, XIcon } from 'lucide-react'
import type { IncidentRecord } from '@/lib/data/api/incidents'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Eyebrow } from '@/components/phlo/page'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/menu'

export type FilterKey = 'kind' | 'owner' | 'status'
export type Filters = Partial<Record<FilterKey, string>>
export const applyFilters = (items: Array<IncidentRecord>, filters: Filters) =>
  items.filter((item) =>
    (Object.keys(filters) as Array<FilterKey>).every(
      (key) => !filters[key] || (item[key] ?? 'Unassigned') === filters[key],
    ),
  )

export function FilterChip({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value?: string
  options: Array<string>
  onChange: (value?: string) => void
}) {
  const active = value !== undefined
  return (
    <span
      className={cn(
        'inline-flex h-10 items-center rounded-full border text-[13px] sm:h-[30px]',
        active
          ? 'border-foreground bg-foreground text-background'
          : 'border-dashed border-skip-line bg-card text-text-2',
      )}
    >
      <DropdownMenu>
        <DropdownMenuTrigger
          className={cn(
            'inline-flex h-full cursor-pointer items-center gap-1.5 rounded-full px-3 outline-none',
            active && 'pr-1.5',
          )}
        >
          {active ? (
            <>
              {label} <span className="opacity-75">·</span> {cap(value)}
            </>
          ) : (
            <>
              <PlusIcon className="size-3" /> {label}
            </>
          )}
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          {options.map((option) => (
            <DropdownMenuItem
              key={option}
              onClick={() => onChange(option === value ? undefined : option)}
            >
              <CheckIcon
                className={cn(
                  'size-3.5',
                  option === value ? 'text-primary' : 'invisible',
                )}
              />{' '}
              {cap(option)}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {active ? (
        <button
          type="button"
          aria-label={`Clear ${label.toLowerCase()} filter`}
          onClick={() => onChange()}
          className="mr-1 inline-flex size-7 items-center justify-center rounded-full sm:size-5"
        >
          <XIcon className="size-3" />
        </button>
      ) : null}
    </span>
  )
}

const age = (date: string) => {
  const minutes = Math.max(
    0,
    Math.floor((Date.now() - new Date(date).getTime()) / 60_000),
  )
  if (minutes < 60) return `${minutes} min`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} h`
  return `${Math.floor(minutes / 1440)} d`
}
const cap = (value: string) =>
  value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())

export function IncidentGroup({
  title,
  note,
  incidents,
}: {
  title: string
  note?: string
  incidents: Array<IncidentRecord>
  headed?: boolean
}) {
  return (
    <section className="flex flex-col gap-2.5 px-4 pt-3 md:gap-0 md:px-0 md:pt-0">
      <div className="flex items-baseline gap-2 md:px-5 md:py-3">
        <Eyebrow>{title}</Eyebrow>
        {note ? (
          <span className="ml-auto text-[12.5px] text-muted-foreground">
            {note}
          </span>
        ) : null}
      </div>
      <div
        aria-hidden
        className="hidden h-9 grid-cols-[64px_minmax(0,1fr)_140px_140px_90px] items-center gap-4 border-y border-line bg-raised px-5 text-xs text-muted-foreground md:grid"
      >
        <span>ID</span>
        <span>Incident</span>
        <span>Status</span>
        <span>Owner</span>
        <span className="text-right">Updated</span>
      </div>
      <ul className="m-0 list-none overflow-hidden rounded-xl border border-border-card p-0 md:rounded-none md:border-0">
        {incidents.map((incident) => (
          <li
            key={incident.id}
            className="border-b border-line-soft last:border-b-0"
          >
            <Link
              to="/incidents/$incidentId"
              params={{ incidentId: incident.id }}
              className="flex min-h-16 items-center gap-3 px-3.5 py-3 text-foreground hover:bg-raised md:grid md:min-h-12 md:grid-cols-[64px_minmax(0,1fr)_140px_140px_90px] md:gap-4 md:px-5 md:py-0"
            >
              <span className="hidden font-mono text-xs text-muted-foreground md:block">
                #{incident.id}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">
                  {incident.title}
                </span>
                <span className="block truncate font-mono text-xs text-muted-foreground">
                  #{incident.id} · {incident.asset_id} · {cap(incident.kind)}
                </span>
              </span>
              <Badge
                variant={
                  incident.status === 'resolved'
                    ? 'ok'
                    : incident.status === 'acknowledged'
                      ? 'warn'
                      : 'bad'
                }
              >
                {cap(incident.status)}
              </Badge>
              <span className="hidden truncate text-[13px] text-text-2 md:block">
                {incident.owner ?? 'Unassigned'}
              </span>
              <span className="ml-auto text-xs text-muted-foreground md:ml-0 md:text-right">
                {age(incident.updated_at)}
              </span>
              <ChevronRightIcon
                className="size-4 text-faint md:hidden"
                aria-hidden
              />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value?: string
  options: Array<string>
  onChange: (value?: string) => void
}) {
  return (
    <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
      <span>{label}</span>
      <select
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value || undefined)}
        className={cn(
          'h-9 rounded-lg border border-input bg-card px-2 text-foreground',
        )}
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {cap(option)}
          </option>
        ))}
      </select>
    </label>
  )
}
