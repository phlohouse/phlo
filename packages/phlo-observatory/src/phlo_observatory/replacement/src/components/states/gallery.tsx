/** Demonstrates the application's running, failure, empty, and loading states. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import {
  CheckIcon,
  CircleCheckIcon,
  CircleXIcon,
  LoaderCircleIcon,
  LockIcon,
  SearchIcon,
  SearchXIcon,
  TriangleAlertIcon,
} from 'lucide-react'
import { Eyebrow } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Dot, Mono } from '@/components/phlo/status'
import { Avatar } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

/** A titled frame around one state. */
export function StateFrame({
  n,
  title,
  where,
  className,
  children,
}: {
  n: number
  title: string
  where: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <section
      className="flex min-w-0 flex-col gap-2.5"
      aria-labelledby={`state-${n}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h2 id={`state-${n}`} className="m-0 text-sm font-semibold">
          {n} · {title}
        </h2>
        <span className="text-[12.5px] text-muted-foreground">{where}</span>
      </div>
      <div
        className={cn(
          'flex flex-1 flex-col overflow-hidden rounded-xl border border-border-card bg-card',
          className,
        )}
      >
        {children}
      </div>
    </section>
  )
}

/* ---------- 1. Job running ---------- */
export function JobRunning() {
  const [pct, setPct] = React.useState(62)
  const [cancelled, setCancelled] = React.useState(false)
  React.useEffect(() => {
    if (cancelled) return
    const t = setInterval(() => setPct((p) => (p >= 96 ? 62 : p + 2)), 1200)
    return () => clearInterval(t)
  }, [cancelled])
  const steps: Array<{
    name: string
    state: 'done' | 'run' | 'wait'
    right: string
  }> = [
    { name: 'extract', state: 'done', right: '41 s' },
    {
      name: 'normalize',
      state: cancelled ? 'wait' : 'run',
      right: cancelled ? 'cancelled' : `${pct}%`,
    },
    { name: 'load', state: 'wait', right: 'waiting' },
    { name: 'materialize', state: 'wait', right: 'waiting' },
  ]
  return (
    <div className="flex flex-1 flex-col gap-3.5 p-[18px]">
      <div className="flex items-center gap-2">
        {cancelled ? (
          <Badge>Cancelled</Badge>
        ) : (
          <Badge variant="info">
            <Dot tone="info" /> Running
          </Badge>
        )}
        <span className="text-[13px] text-muted-foreground">
          Backfill · started 2 min ago
        </span>
      </div>
      <div>
        <div className="font-mono text-base font-medium">ingest_bioreactor</div>
        <div className="text-[13px] text-muted-foreground">
          on <Mono className="text-xs text-branch">fix/telemetry-schema</Mono> ·
          partition 4 of 10
        </div>
      </div>
      <div className="flex flex-col gap-2.5">
        {steps.map((s) => (
          <div
            key={s.name}
            className="grid grid-cols-[18px_90px_1fr_64px] items-center gap-x-2.5 text-[13px]"
          >
            {s.state === 'done' ? (
              <CheckIcon
                className="size-3.5 text-ok"
                strokeWidth={2.5}
                aria-label="Done"
              />
            ) : s.state === 'run' ? (
              <span
                className="ml-[3px] size-2 rounded-full bg-primary"
                aria-label="Running"
              />
            ) : (
              <span
                className="ml-[3px] box-border size-2 rounded-full border-[1.5px] border-skip-line"
                aria-label="Waiting"
              />
            )}
            <span
              className={cn(
                'font-mono text-[12.5px]',
                s.state === 'wait' && 'text-muted-foreground',
              )}
            >
              {s.name}
            </span>
            <div
              className="relative h-2 overflow-hidden rounded-[3px] bg-soft"
              role="progressbar"
              aria-label={s.name}
              aria-valuenow={
                s.state === 'done' ? 100 : s.state === 'run' ? pct : 0
              }
            >
              {s.state === 'done' ? (
                <div className="absolute inset-0 rounded-[3px] bg-ok-bar" />
              ) : null}
              {s.state === 'run' ? (
                <div
                  className="progress-stripe absolute inset-y-0 left-0 rounded-[3px] transition-[width]"
                  style={{ width: `${pct}%` }}
                />
              ) : null}
            </div>
            <span
              className={cn(
                'text-right text-[11.5px] text-muted-foreground',
                s.state !== 'wait' && 'font-mono',
              )}
            >
              {s.right}
            </span>
          </div>
        ))}
      </div>
      <pre className="m-0 overflow-x-auto rounded-lg bg-sunken px-3 py-2.5 font-mono text-[11.5px] leading-[19px] whitespace-pre text-text-3">
        {`09:52:14 normalize  rows 11,420 / 18,400
09:52:15 normalize  do_sat_pct  ok
09:52:16 normalize  rows 11,904 / 18,400
09:52:17 normalize  rows 12,388 / 18,400
09:52:18 normalize  rows 12,871 / 18,400`}
      </pre>
      <div className="mt-auto flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          disabled={cancelled}
          onClick={() => setCancelled(true)}
        >
          Cancel run
        </Button>
        <Link
          to="/pipelines/$jobName"
          params={{ jobName: 'ingest_bioreactor' }}
          className={buttonVariants({ variant: 'outline' })}
        >
          Follow logs
        </Link>
        <span className="ml-auto text-[13px] text-muted-foreground">
          {cancelled ? 'Stopped at partition 4' : '~3 min left'}
        </span>
      </div>
    </div>
  )
}

/* ---------- 2. Query failed ---------- */
export function QueryFailed() {
  const [fixed, setFixed] = React.useState(false)
  const kw = 'text-code-kw'
  const fn = 'text-link'
  return (
    <>
      <div className="flex border-b border-line bg-card font-mono text-[12.5px] leading-[22px]">
        <div
          aria-hidden
          className="w-11 shrink-0 border-r border-line bg-raised py-2.5 pr-3 text-right text-faint"
        >
          {[1, 2, 3, 4, 5].map((n) => (
            <div key={n}>{n}</div>
          ))}
        </div>
        <pre
          className="m-0 min-w-0 flex-1 overflow-x-auto px-4 py-2.5 whitespace-pre"
          aria-label="Failed query"
        >
          <span className={kw}>SELECT</span> minute,{'\n'}
          {'  '}
          <span className={fn}>avg</span>(ph){'     '}
          <span className={kw}>AS</span> ph,{'\n'}
          {'  '}
          <span className={fn}>avg</span>(
          {fixed ? (
            <span>do_sat_pct</span>
          ) : (
            <span className="underline decoration-bad decoration-wavy underline-offset-[3px]">
              do_pct
            </span>
          )}
          ) <span className={kw}>AS</span> do_pct{'\n'}
          <span className={kw}>FROM</span> bronze.bioreactor_telemetry{'\n'}
          <span className={kw}>WHERE</span> run_id ={' '}
          <span className="text-ok-text">'BR-2026-121'</span>
        </pre>
      </div>
      {fixed ? (
        <div
          role="status"
          className="flex h-[38px] items-center gap-2 border-b border-line bg-ok-wash px-3.5 text-[12.5px] text-ok-ink"
        >
          <CircleCheckIcon className="size-3.5" /> Fixed · 360 rows in 1.4 s
        </div>
      ) : (
        <div
          role="alert"
          className="flex h-[38px] items-center gap-2 border-b border-line bg-bad-wash px-3.5 text-[12.5px] text-bad-ink"
        >
          <CircleXIcon className="size-3.5" /> Failed after 0.2 s · nothing was
          read
        </div>
      )}
      <div className="flex flex-col gap-3 p-4">
        <div className="text-sm font-medium">
          Column <Mono className="text-[13px]">do_pct</Mono> doesn’t exist on
          this branch
        </div>
        <div className="text-[13px] leading-normal text-muted-foreground">
          You’re querying{' '}
          <Mono className="text-xs text-branch">fix/telemetry-schema</Mono>,
          where it was renamed at v7. Line 3, column 7.
        </div>
        <pre className="m-0 rounded-[10px] border border-bad-line bg-bad-wash px-3.5 py-3 font-mono text-[11.5px] leading-[1.7] whitespace-pre-wrap text-bad-ink">
          {`Binder Error: Referenced column "do_pct" not found.
Candidate bindings: "do_sat_pct"`}
        </pre>
        <div className="flex flex-wrap gap-2">
          <Button disabled={fixed} onClick={() => setFixed(true)}>
            Use do_sat_pct
          </Button>
          <Link to="/query" className={buttonVariants({ variant: 'outline' })}>
            Run on main instead
          </Link>
        </div>
      </div>
    </>
  )
}

/* ---------- 3. Nothing found ---------- */
export function NothingFound() {
  const [q, setQ] = React.useState('bioreacter')
  const [filters, setFilters] = React.useState(['Gold', 'Needs attention'])
  return (
    <>
      <div className="border-b border-line px-3.5 py-3">
        <label className="flex h-8 items-center gap-2 rounded-lg border border-primary bg-card px-2.5 ring-3 ring-primary-soft">
          <SearchIcon className="size-3.5 text-muted-foreground" aria-hidden />
          <span className="sr-only">Filter assets</span>
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="min-w-0 flex-1 border-0 bg-transparent text-[13.5px] text-foreground outline-none"
          />
        </label>
      </div>
      {filters.length ? (
        <div className="flex gap-1.5 border-b border-line px-3.5 py-2.5">
          {filters.map((f) => (
            <span
              key={f}
              className="flex h-[26px] items-center rounded-full bg-foreground px-3 text-xs text-background"
            >
              {f}
            </span>
          ))}
        </div>
      ) : null}
      <EmptyState
        className="flex-1 justify-center gap-3 rounded-none bg-transparent py-6 [&>span:first-child]:size-11 [&>span:first-child]:rounded-xl"
        icon={<SearchXIcon className="size-5" />}
        title={<span className="text-[15px]">No tables match “{q}”</span>}
        action={
          <div className="mt-1 flex flex-col items-center gap-2">
            <Link
              to="/assets"
              search={{ q: 'bioreactor', filter: 'all' }}
              className="text-[13.5px]"
            >
              Did you mean <Mono>bioreactor</Mono>? 6 tables
            </Link>
            {filters.length ? (
              <Button variant="outline" onClick={() => setFilters([])}>
                Clear {filters.length} filters
              </Button>
            ) : null}
          </div>
        }
      >
        <span className="text-[13px] leading-normal">
          {filters.length
            ? 'Two filters are also on, which hides most tables.'
            : 'Check the spelling, or search by owner or tag.'}
        </span>
      </EmptyState>
    </>
  )
}

/* ---------- 4. Not allowed ---------- */
export function NotAllowed() {
  const [asked, setAsked] = React.useState(false)
  return (
    <>
      <div className="flex flex-col gap-1 border-b border-line px-[18px] py-4">
        <div className="text-[15px] font-semibold">
          Merge into <Mono className="text-sm">main</Mono>
        </div>
        <div className="text-[13px] text-muted-foreground">
          <Mono className="text-xs">fix/elisa-dilution-factor</Mono> · 1 commit
        </div>
      </div>
      <div className="flex flex-col gap-3.5 px-[18px] py-4">
        <div className="flex items-center gap-2 rounded-[10px] border border-ok-line bg-ok-wash px-3 py-2 text-[13px] text-ok-ink">
          <CircleCheckIcon className="size-3.5 shrink-0" /> All checks passed
        </div>
        <div className="flex gap-3 rounded-[10px] border border-border bg-raised p-3.5">
          <span className="flex size-[30px] shrink-0 items-center justify-center rounded-lg bg-soft text-muted-foreground">
            <LockIcon className="size-4" aria-hidden />
          </span>
          <div className="flex flex-col gap-1">
            <div className="text-sm font-medium">
              You can’t sign merges into main
            </div>
            <div className="text-[13px] leading-normal text-muted-foreground">
              Your role is Engineer. Merges into{' '}
              <Mono className="text-xs">main</Mono> need an Approver’s
              signature.
            </div>
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium">Ask an approver</span>
          <div className="flex flex-wrap gap-1.5">
            <span className="inline-flex h-[26px] items-center gap-1.5 rounded-md bg-soft px-2 text-[12.5px]">
              <Avatar initials="GP" className="size-4 text-[7px]" /> Gareth
            </span>
            <span className="inline-flex h-[26px] items-center gap-1.5 rounded-md bg-soft px-2 text-[12.5px]">
              <Avatar initials="SR" tone="teal" className="size-4 text-[7px]" />{' '}
              Sam R.
            </span>
            <span className="inline-flex h-[26px] items-center rounded-md bg-soft px-2 text-[12.5px] text-muted-foreground">
              Lee W. · signing not set up
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={asked} onClick={() => setAsked(true)}>
            {asked ? 'Approval requested' : 'Request approval'}
          </Button>
          <Link
            to="/settings/members"
            className={buttonVariants({ variant: 'outline' })}
          >
            Who can approve?
          </Link>
        </div>
        {asked ? (
          <p role="status" className="m-0 text-[12.5px] text-muted-foreground">
            Gareth and Sam R. have been asked to sign.
          </p>
        ) : null}
      </div>
    </>
  )
}

/* ---------- 5. Catalog unreachable ---------- */
export function CatalogDown() {
  const [secs, setSecs] = React.useState(20)
  const [retrying, setRetrying] = React.useState(false)
  React.useEffect(() => {
    const t = setInterval(() => setSecs((s) => (s <= 1 ? 20 : s - 1)), 1000)
    return () => clearInterval(t)
  }, [])
  const stats: Array<{ label: string; value: string; live: boolean }> = [
    { label: 'Asset freshness', value: '141 / 148', live: false },
    { label: 'Branches', value: '4', live: false },
    { label: 'Dagster runs', value: '312', live: true },
    { label: 'Audits', value: '425 / 431', live: true },
  ]
  const services: Array<{
    name: string
    tone: 'ok' | 'bad' | 'warn'
    state: string
  }> = [
    { name: 'Dagster', tone: 'ok', state: 'up' },
    { name: 'Nessie catalog', tone: 'bad', state: 'down 4 min' },
    { name: 'Postgres', tone: 'ok', state: 'up' },
    { name: 'Object store', tone: 'warn', state: 'slow' },
  ]
  return (
    <>
      <div
        role="alert"
        className="flex gap-2.5 border-b border-bad-line bg-bad-wash px-3.5 py-3 text-[13px] leading-snug text-bad-ink"
      >
        <TriangleAlertIcon className="mt-px size-4 shrink-0" aria-hidden />
        <span>
          <strong className="font-semibold">
            Can’t reach the Nessie catalog.
          </strong>{' '}
          Showing what we last saw at 09:37. Branching, merging and backfills
          are paused until it’s back.
        </span>
      </div>
      <div className="flex items-center gap-2.5 border-b border-line px-3.5 py-2 text-[12.5px]">
        <span className="text-muted-foreground" aria-live="polite">
          {retrying ? 'Retrying…' : `Retrying in ${secs} s`}
        </span>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto h-10 sm:h-[26px] sm:text-[12.5px]"
          disabled={retrying}
          onClick={() => {
            setRetrying(true)
            setTimeout(() => {
              setRetrying(false)
              setSecs(20)
            }, 900)
          }}
        >
          Retry now
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-10 sm:h-[26px] sm:text-[12.5px]"
        >
          Status page
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-2.5 p-3.5">
        {stats.map((s) => (
          <div
            key={s.label}
            className={cn(
              'flex flex-col gap-1 rounded-[10px] border border-line px-3.5 py-3',
              !s.live && 'opacity-55',
            )}
          >
            <Eyebrow>{s.label}</Eyebrow>
            <div className="text-xl font-medium">{s.value}</div>
            <Badge variant={s.live ? 'ok' : 'neutral'} size="sm">
              {s.live ? 'live' : '4 min old'}
            </Badge>
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-2 px-3.5 pb-3.5">
        <Eyebrow>Services</Eyebrow>
        {services.map((s) => (
          <div key={s.name} className="flex items-center gap-2 text-[13.5px]">
            <Dot tone={s.tone} />
            {s.name}
            <span
              className={cn(
                'ml-auto font-mono text-xs',
                s.tone === 'bad' ? 'text-bad-text' : 'text-muted-foreground',
              )}
            >
              {s.state}
            </span>
          </div>
        ))}
      </div>
    </>
  )
}

/* ---------- 6. First load ---------- */
export function FirstLoad() {
  return (
    <div
      className="flex flex-1 flex-col gap-3.5 p-4"
      aria-busy="true"
      aria-label="Loading overview"
    >
      <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
        <LoaderCircleIcon
          className="size-3.5 animate-spin text-link"
          aria-hidden
        />{' '}
        Connecting to Dagster and Nessie…
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <div className="flex flex-col gap-2.5 rounded-[10px] border border-line px-3.5 py-3">
          <Skeleton className="h-2.5 w-3/5" />
          <Skeleton className="h-[22px] w-[45%]" />
          <Skeleton className="h-[5px]" />
        </div>
        <div className="flex flex-col gap-2.5 rounded-[10px] border border-line px-3.5 py-3">
          <Skeleton className="h-2.5 w-1/2" />
          <Skeleton className="h-[22px] w-[35%]" />
          <Skeleton className="h-2.5 w-[70%]" />
        </div>
      </div>
      <div className="flex flex-col gap-3 rounded-[10px] border border-line px-3.5 py-3">
        <Skeleton className="h-3 w-[30%]" />
        <div className="flex gap-2">
          <Skeleton className="h-[70px] flex-1" />
          <Skeleton className="h-[70px] flex-1" />
          <Skeleton className="h-[70px] flex-1" />
        </div>
      </div>
      <div className="flex flex-1 flex-col gap-3 rounded-[10px] border border-line px-3.5 py-3">
        <Skeleton className="h-3 w-2/5" />
        {['w-full', 'w-[70%]', 'w-[85%]'].map((w) => (
          <div key={w} className="flex items-center gap-2.5">
            <Skeleton className="size-2 rounded-full" />
            <Skeleton className={cn('h-3', w)} />
          </div>
        ))}
      </div>
      <div className="text-xs text-muted-foreground">
        Shapes match the real layout so nothing jumps when data arrives.
      </div>
    </div>
  )
}
