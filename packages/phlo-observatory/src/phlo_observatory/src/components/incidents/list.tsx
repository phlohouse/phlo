/** Filters and renders grouped incident records. */
import { Link } from '@tanstack/react-router'
import {
  CheckIcon,
  ChevronRightIcon,
  FileWarningIcon,
  PlusIcon,
  XIcon,
} from 'lucide-react'
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
        className="hidden h-9 items-center gap-x-4 border-b border-line bg-raised px-4 text-xs tracking-wide text-muted-foreground md:grid md:grid-cols-[56px_minmax(0,1fr)_76px_150px_60px] lg:px-5 xl:grid-cols-[62px_minmax(0,1fr)_92px_84px_160px_110px_70px]"
      >
        <span>ID</span>
        <span>Incident</span>
        <span className="hidden xl:block">Layer</span>
        <span>Severity</span>
        <span>Status</span>
        <span className="hidden xl:block">Owner</span>
        <span className="text-right">Opened</span>
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
              className="flex items-start gap-3 py-3 pr-3 pl-3.5 text-foreground hover:bg-raised md:grid md:h-12 md:grid-cols-[56px_minmax(0,1fr)_76px_150px_60px] md:items-center md:gap-x-4 md:px-4 md:py-0 lg:px-5 xl:grid-cols-[62px_minmax(0,1fr)_92px_84px_160px_110px_70px]"
            >
              <span
                className="hidden truncate font-mono text-[12.5px] text-muted-foreground md:block"
                title={incident.id}
              >
                #{incident.id}
              </span>
              <span
                className="mt-px inline-flex size-7 shrink-0 items-center justify-center rounded-md bg-soft text-text-3 md:hidden"
                aria-hidden
              >
                <FileWarningIcon className="size-4" />
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-[5px] md:flex-row md:items-center md:gap-2.5">
                <span
                  className="hidden size-7 shrink-0 items-center justify-center rounded-md bg-soft text-text-3 md:inline-flex"
                  aria-hidden
                >
                  <FileWarningIcon className="size-4" />
                </span>
                <span className="truncate text-[15px] font-medium md:text-sm md:font-normal">
                  {incident.title}
                </span>
                <span className="truncate font-mono text-xs text-muted-foreground md:hidden">
                  #{incident.id} · {incident.asset_id} · {cap(incident.kind)}
                </span>
                <span className="hidden text-[13px] whitespace-nowrap text-muted-foreground lg:inline">
                  {cap(incident.kind)}
                </span>
                <span className="flex items-center gap-2.5 text-[13px] md:hidden">
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
                  <span className="truncate text-xs text-muted-foreground">
                    {incident.owner ?? 'Unassigned'}
                  </span>
                  <span className="ml-auto shrink-0 text-[12.5px] text-muted-foreground">
                    {age(incident.created_at)}
                  </span>
                </span>
              </span>
              <span
                className="hidden text-[13px] text-muted-foreground xl:block"
                title="Layer is not supplied by the incident API"
              >
                Unavailable
              </span>
              <span
                className="hidden text-[13.5px] text-muted-foreground md:block"
                title="Severity is not supplied by the incident API"
              >
                Unknown
              </span>
              <span className="hidden min-w-0 md:flex">
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
              </span>
              <span className="hidden truncate text-[13.5px] text-text-2 xl:block">
                {incident.owner ?? 'Unassigned'}
              </span>
              <span className="hidden text-right text-[13px] text-muted-foreground md:block">
                {age(incident.created_at)}
              </span>
              <ChevronRightIcon
                className="mt-[3px] size-3.5 shrink-0 text-faint md:hidden"
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
