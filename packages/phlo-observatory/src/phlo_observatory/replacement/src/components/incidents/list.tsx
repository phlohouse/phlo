/** Filters and renders grouped incident records. */
import { Link } from '@tanstack/react-router'
import { ChevronRightIcon } from 'lucide-react'
import type { IncidentRecord } from '@/lib/data/api/incidents'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Eyebrow } from '@/components/phlo/page'

export type FilterKey = 'kind' | 'owner' | 'status'
export type Filters = Partial<Record<FilterKey, string>>
export const applyFilters = (items: Array<IncidentRecord>, filters: Filters) =>
  items.filter((item) =>
    (Object.keys(filters) as Array<FilterKey>).every(
      (key) => !filters[key] || (item[key] ?? 'Unassigned') === filters[key],
    ),
  )

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
        className="hidden h-9 grid-cols-[minmax(0,1fr)_140px_140px_90px] items-center gap-4 border-y border-line bg-raised px-5 text-xs text-muted-foreground md:grid"
      >
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
              className="flex min-h-16 items-center gap-3 px-3.5 py-3 text-foreground hover:bg-raised md:grid md:min-h-12 md:grid-cols-[minmax(0,1fr)_140px_140px_90px] md:gap-4 md:px-5 md:py-0"
            >
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
