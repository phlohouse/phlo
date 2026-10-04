/** Reference dashboard composition backed by environment-scoped observations. */
import * as React from 'react'
import {
  Link,
  createFileRoute,
  getRouteApi,
  useRouter,
  useRouterState,
} from '@tanstack/react-router'
import { ArrowRightIcon, CircleCheckIcon, RefreshCwIcon } from 'lucide-react'
import { z } from 'zod'
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import type { ObservatoryServiceList } from '@/lib/data/api/client'
import type { ApiRun } from '@/lib/data/api/pipelines'
import type { Layer } from '@/lib/data/types'
import type { ChartConfig } from '@/components/ui/chart'
import { serviceHealthLabel, serviceHealthTone } from '@/lib/data/api/client'
import { getOverview, overviewRangeSchema } from '@/lib/data/api/core'
import { Eyebrow, PageBody, PageHeader } from '@/components/phlo/page'
import { KpiCard } from '@/components/phlo/kpi'
import { Dot, HealthBar, LayerSwatch, toneText } from '@/components/phlo/status'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/toggle-group'

type Range = z.infer<typeof overviewRangeSchema>
type Service = ObservatoryServiceList['items'][number]
type Overview = Awaited<ReturnType<typeof getOverview>>

export const Route = createFileRoute('/_app/')({
  validateSearch: z.object({ range: overviewRangeSchema.default('24h') }),
  loaderDeps: ({ search }) => ({ env: search.env, range: search.range }),
  loader: ({ deps }) => getOverview({ data: deps }),
  head: () => ({ meta: [{ title: 'Overview · phlo' }] }),
  component: OverviewPage,
})

export function filterServices(
  services: Array<Service>,
  name: string,
  status: Service['status'] | '',
) {
  const query = name.trim().toLowerCase()
  return services.filter(
    (service) =>
      service.id.toLowerCase().includes(query) &&
      (!status || service.status === status),
  )
}

export function runsInRange(
  runs: Array<ApiRun>,
  range: Range,
  observedAt: string,
) {
  const hours = range === '24h' ? 24 : range === '7d' ? 168 : 720
  const end = Date.parse(observedAt)
  return runs.filter((run) => {
    const created = Date.parse(run.created_at)
    return created >= end - hours * 3_600_000 && created <= end
  })
}

export function runBuckets(
  runs: Array<ApiRun>,
  range: Range,
  observedAt: string,
) {
  const width = range === '24h' ? 3_600_000 : 86_400_000
  const count = range === '24h' ? 24 : range === '7d' ? 7 : 30
  const start = Date.parse(observedAt) - count * width
  const rows = Array.from({ length: count }, (_, index) => ({
    timestamp: new Date(start + index * width).toISOString(),
    succeeded: 0,
    failed: 0,
  }))
  for (const run of runsInRange(runs, range, observedAt)) {
    const index = Math.min(
      count - 1,
      Math.floor((Date.parse(run.created_at) - start) / width),
    )
    const row = rows[index]
    if (row && run.status === 'SUCCESS') row.succeeded += 1
    if (row && run.status === 'FAILURE') row.failed += 1
  }
  return rows
}

function OverviewKpis({ data }: { data: Overview }) {
  const { overview, range, observedAt } = data
  const fresh = overview.freshness_counts
  const quality = overview.quality_checks.counts
  const runs = runsInRange(data.runs.items, range, observedAt)
  const failed = runs.filter((run) => run.status === 'FAILURE').length
  const succeeded = runs.filter((run) => run.status === 'SUCCESS').length
  const durations = runs
    .filter((run) => run.status === 'SUCCESS' && run.duration_seconds !== null)
    .map((run) => run.duration_seconds ?? 0)
    .sort((a, b) => a - b)
  const middle = Math.floor(durations.length / 2)
  const median = durations.length
    ? ((durations[middle] ?? 0) +
        (durations[Math.floor((durations.length - 1) / 2)] ?? 0)) /
      2
    : null
  const open =
    (overview.incident_counts.open ?? 0) +
    (overview.incident_counts.acknowledged ?? 0)
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
      <KpiCard
        label="Asset freshness"
        value={
          fresh.unknown || !overview.asset_count
            ? '—'
            : `${Math.round((fresh.fresh / overview.asset_count) * 100)}%`
        }
        qualifier={`${fresh.fresh} of ${overview.asset_count} fresh`}
        to="/assets"
        env={overview.env}
        footer={
          <div className="flex flex-col gap-1.5">
            <HealthBar
              ok={fresh.fresh}
              bad={fresh.stale}
              unknown={fresh.unknown}
            />
            <span>
              {fresh.stale} stale · {fresh.unknown} unknown
            </span>
          </div>
        }
      />
      <KpiCard
        label="Dagster runs"
        value={runs.length}
        qualifier={`${failed} failed · observed`}
        qualifierTone={failed ? 'bad' : 'muted'}
        to="/pipelines/timeline"
        env={overview.env}
        footer={`${succeeded} succeeded · median ${median === null ? 'unavailable' : `${median.toFixed(1)} s`} · latest 100 records only`}
      />
      <KpiCard
        label="Audits"
        value={quality?.passing ?? '—'}
        qualifier={quality ? `of ${quality.total} passing` : 'not observed'}
        footer={
          quality
            ? `${quality.total - quality.passing - quality.unevaluated} failing · ${quality.unevaluated} unevaluated`
            : 'Quality observations unavailable'
        }
      />
      <KpiCard
        label="Open incidents"
        value={open}
        qualifier={`${overview.incident_counts.acknowledged ?? 0} acknowledged`}
        to="/incidents"
        env={overview.env}
        footer="Persisted incidents in this environment"
      />
    </div>
  )
}

function allClear(overview: Overview['overview']) {
  const fresh = overview.freshness_counts
  const quality = overview.quality_checks.counts
  return (
    overview.asset_count > 0 &&
    fresh.unknown === 0 &&
    fresh.stale === 0 &&
    fresh.fresh === overview.asset_count &&
    quality !== null &&
    quality.total > 0 &&
    overview.quality_checks.status === 'available' &&
    quality.unevaluated === 0 &&
    quality.passing === quality.total &&
    (overview.incident_counts.open ?? 0) +
      (overview.incident_counts.acknowledged ?? 0) ===
      0
  )
}

function OverviewPage() {
  const data = Route.useLoaderData()
  const { overview, range, observedAt } = data
  const { services } = getRouteApi('/_app').useLoaderData()
  const router = useRouter()
  const navigate = Route.useNavigate()
  const busy = useRouterState({ select: (state) => state.isLoading })
  React.useEffect(() => {
    const timer = window.setInterval(() => void router.invalidate(), 60_000)
    return () => window.clearInterval(timer)
  }, [router])
  const runs = runsInRange(data.runs.items, range, observedAt)
  const activity = [
    ...runs.map((run) => ({
      id: run.run_id,
      at: run.created_at,
      text: `${run.job_id} · ${run.status.toLowerCase()}`,
      tone:
        run.status === 'SUCCESS'
          ? ('ok' as const)
          : run.status === 'FAILURE'
            ? ('bad' as const)
            : ('neutral' as const),
      run,
    })),
    ...data.incidents.items
      .filter(
        (incident) =>
          Date.parse(incident.updated_at) >=
          Date.parse(observedAt) -
            (range === '24h' ? 24 : range === '7d' ? 168 : 720) * 3_600_000,
      )
      .map((incident) => ({
        id: incident.id,
        at: incident.updated_at,
        text: incident.title,
        tone:
          incident.status === 'resolved' ? ('ok' as const) : ('warn' as const),
        incident,
      })),
  ]
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
    .slice(0, 6)
  return (
    <>
      <PageHeader
        title="Overview"
        meta={`Observed ${formatTimestamp(observedAt)} · auto every 60 s`}
        actions={
          <>
            <Segmented
              aria-label="Time range"
              value={range}
              onValueChange={(value) =>
                void navigate({
                  search: (previous) => ({ ...previous, range: value }),
                })
              }
              options={[
                { value: '24h', label: '24 h' },
                { value: '7d', label: '7 d' },
                { value: '30d', label: '30 d' },
              ]}
            />
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void router.invalidate()}
            >
              <RefreshCwIcon className={busy ? 'animate-spin' : ''} /> Refresh
            </Button>
          </>
        }
      />
      <PageBody>
        {allClear(overview) ? (
          <div className="flex items-center gap-3 rounded-xl border border-ok-line bg-ok-wash px-4 py-3 text-ok-ink">
            <CircleCheckIcon className="size-5" />
            <div className="flex flex-col">
              <span className="font-medium">All clear</span>
              <span className="text-[13px]">
                All {overview.asset_count} assets meet their freshness targets
                and all evaluated audits passed.
              </span>
            </div>
          </div>
        ) : null}
        <OverviewKpis data={data} />
        <Card>
          <CardHeader>
            <CardTitle>Data flow</CardTitle>
            <CardDescription className="hidden sm:block">
              Sources through each layer, as of the last materialization
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col items-stretch gap-2.5 lg:flex-row">
            <div className="flex shrink-0 flex-col gap-2 rounded-[10px] bg-sunken p-3.5 lg:w-[230px]">
              <Eyebrow>Sources</Eyebrow>
              {data.sources.items.length ? (
                data.sources.items.map((source) => (
                  <div
                    key={source.id}
                    className="flex min-w-0 items-center gap-2 text-[13.5px] text-foreground"
                  >
                    <Dot tone="neutral" />
                    <Link
                      to="/assets/$assetId"
                      params={{ assetId: source.id }}
                      search={{ env: overview.env }}
                      className="truncate hover:text-link"
                    >
                      {source.id}
                    </Link>
                  </div>
                ))
              ) : (
                <span className="text-[13px] text-muted-foreground">
                  No source assets registered
                </span>
              )}
              <span className="text-xs text-muted-foreground">
                Source lag is not observed
                {data.sources.next_cursor ? ' · more sources available' : ''}.
              </span>
            </div>
            {(['bronze', 'silver', 'gold'] satisfies Array<Layer>).map(
              (layer) => (
                <React.Fragment key={layer}>
                  <ArrowRightIcon
                    className="hidden size-5 shrink-0 self-center text-faint lg:block"
                    aria-hidden
                  />
                  <LayerCard layer={layer} data={data} />
                </React.Fragment>
              ),
            )}
          </CardContent>
          <div className="px-6 pb-4 text-xs text-muted-foreground">
            {overview.asset_count} total assets ·{' '}
            {overview.materialized_asset_count} verified materializations. Layer
            counts require explicit layer metadata or a Bronze, Silver or Gold
            group. Other groups remain unclassified.
          </div>
        </Card>
        <div className="grid shrink-0 grid-cols-1 gap-4 lg:flex-1 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <RunsByHour data={data} />
          <Card>
            <CardHeader>
              <CardTitle>Recent activity</CardTitle>
              <CardAction>
                <Link
                  to="/incidents"
                  search={{ env: overview.env }}
                  className="text-[13px]"
                >
                  All incidents
                </Link>
              </CardAction>
            </CardHeader>
            <CardContent className="flex flex-col gap-3.5">
              {activity.map((item) => (
                <div key={item.id} className="flex gap-3">
                  <Dot tone={item.tone} size="md" className="mt-1.5" />
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <div className="text-sm leading-snug">
                      {'run' in item ? (
                        <Link
                          to="/pipelines/$jobName"
                          params={{ jobName: item.run.job_id }}
                          search={{ env: overview.env, run: item.run.run_id }}
                          className="break-all text-foreground hover:text-link"
                        >
                          {item.text}
                        </Link>
                      ) : (
                        <Link
                          to="/incidents/$incidentId"
                          params={{ incidentId: item.incident.id }}
                          search={{ env: overview.env }}
                          className="text-foreground hover:text-link"
                        >
                          {item.text}
                        </Link>
                      )}
                    </div>
                    <div className="text-[13px] text-muted-foreground">
                      {formatTimestamp(item.at)}
                    </div>
                  </div>
                </div>
              ))}
              {!activity.length ? (
                <p className="m-0 text-sm text-muted-foreground">
                  No activity observed in this time range.
                </p>
              ) : null}
              {data.incidents.next_cursor ? (
                <p className="m-0 text-xs text-muted-foreground">
                  Incident activity is limited to the first 500 records.
                </p>
              ) : null}
            </CardContent>
          </Card>
        </div>
        <details id="services" className="shrink-0 text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Environment services · {services.length} definitions
          </summary>
          <div className="mt-3">
            <ServiceHealth services={services} />
          </div>
        </details>
      </PageBody>
    </>
  )
}

function LayerCard({ layer, data }: { layer: Layer; data: Overview }) {
  const groups = data.layers.items.filter(
    (item) => (item.layer ?? item.group_name?.toLowerCase()) === layer,
  )
  const count = groups.reduce((sum, group) => sum + group.asset_count, 0)
  const materialized = groups.reduce(
    (sum, group) => sum + group.materialized_asset_count,
    0,
  )
  const freshness = groups.reduce(
    (sum, group) => ({
      fresh: sum.fresh + (group.freshness_counts?.fresh ?? 0),
      stale: sum.stale + (group.freshness_counts?.stale ?? 0),
      unknown:
        sum.unknown + (group.freshness_counts?.unknown ?? group.asset_count),
    }),
    { fresh: 0, stale: 0, unknown: 0 },
  )
  return (
    <Link
      to="/assets"
      search={{ env: data.overview.env, layer }}
      className="flex min-w-0 flex-1 flex-col gap-2.5 rounded-[10px] border border-border-card p-3.5 text-foreground hover:border-border-strong hover:text-foreground"
    >
      <div className="flex items-center gap-2">
        <LayerSwatch layer={layer} size="lg" />
        <span className="text-sm font-medium">
          {layer[0].toUpperCase() + layer.slice(1)}
        </span>
        <span className="ml-auto text-[13px] text-muted-foreground">
          {layer === 'bronze'
            ? 'Raw'
            : layer === 'silver'
              ? 'Cleaned'
              : 'Curated'}
        </span>
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-2xl font-medium">
          {groups.length ? count : '—'}
        </span>
        <span className="text-[13px] text-muted-foreground">assets</span>
      </div>
      <HealthBar
        ok={freshness.fresh}
        bad={freshness.stale}
        unknown={groups.length ? freshness.unknown : 1}
      />
      <div className="text-[13px] text-muted-foreground">
        {groups.length
          ? `${materialized} verified materializations`
          : 'Layer not classified'}{' '}
        · {freshness.stale} stale · {freshness.unknown} unknown
      </div>
    </Link>
  )
}

const runsChartConfig = {
  succeeded: { label: 'Succeeded', color: 'var(--bar)' },
  failed: { label: 'Failed', color: 'var(--bad)' },
} satisfies ChartConfig

function RunsByHour({ data }: { data: Overview }) {
  const rows = runBuckets(data.runs.items, data.range, data.observedAt)
  const totals = rows.reduce(
    (total, row) => ({
      succeeded: total.succeeded + row.succeeded,
      failed: total.failed + row.failed,
    }),
    { succeeded: 0, failed: 0 },
  )
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {data.range === '24h' ? 'Runs by hour' : 'Runs by day'}
        </CardTitle>
        <CardAction className="gap-3.5 text-[13px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-[2px] bg-bar" /> Succeeded
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-[2px] bg-bad" /> Failed
          </span>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-2">
        <ChartContainer
          config={runsChartConfig}
          label={`${totals.succeeded} succeeded and ${totals.failed} failed runs, last ${data.range}`}
          className="aspect-auto h-[180px] w-full lg:h-auto lg:min-h-[180px] lg:flex-1"
        >
          <BarChart
            data={rows}
            margin={{ top: 4, right: 0, bottom: 0, left: 0 }}
            barCategoryGap="18%"
          >
            <CartesianGrid vertical={false} />
            <XAxis dataKey="timestamp" hide />
            <YAxis
              width={24}
              tickLine={false}
              axisLine={false}
              tickMargin={4}
              fontSize={11}
              allowDecimals={false}
              tickCount={4}
            />
            <ChartTooltip
              cursor={false}
              content={
                <ChartTooltipContent
                  labelFormatter={(value) => formatTimestamp(String(value))}
                  valueFormatter={(value) => `${value} runs`}
                />
              }
            />
            <Bar
              dataKey="succeeded"
              stackId="runs"
              fill="var(--color-succeeded)"
              radius={3}
              isAnimationActive={false}
            />
            <Bar
              dataKey="failed"
              stackId="runs"
              fill="var(--color-failed)"
              radius={3}
              isAnimationActive={false}
            />
          </BarChart>
        </ChartContainer>
        <div className="flex justify-between pl-6 font-mono text-[11px] text-muted-foreground">
          {rows
            .filter((_, index) => index % Math.ceil(rows.length / 5) === 0)
            .map((row) => (
              <span key={row.timestamp}>
                {new Intl.DateTimeFormat('en-GB', {
                  timeZone: 'UTC',
                  ...(data.range === '24h'
                    ? { hour: '2-digit', minute: '2-digit' }
                    : { day: 'numeric', month: 'short' }),
                }).format(new Date(row.timestamp))}
              </span>
            ))}
        </div>
        <p className="m-0 text-xs text-muted-foreground">
          Counts use the latest {data.runs.items.length} observed records from a
          bounded API read, not complete history.
        </p>
      </CardContent>
    </Card>
  )
}

function ServiceHealth({ services }: { services: Array<Service> }) {
  const [name, setName] = React.useState('')
  const [status, setStatus] = React.useState<Service['status'] | ''>('')
  const filtered = filterServices(services, name, status)
  const statuses = [
    ...new Set(services.map((service) => service.status)),
  ].sort()
  return (
    <Card>
      <CardHeader>
        <CardTitle>Environment services</CardTitle>
        <p className="m-0 text-[13px] text-muted-foreground">
          Registered services without an observation remain unknown. Unknown
          does not mean unhealthy.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex flex-1 flex-col gap-1.5 text-[13px]">
            Filter by service name
            <Input
              type="search"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Service name"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-[13px]">
            Filter by status
            <select
              value={status}
              onChange={(event) =>
                setStatus(
                  statuses.find((item) => item === event.target.value) ?? '',
                )
              }
              className="h-9 rounded-lg border border-input bg-card px-3 text-foreground"
            >
              <option value="">All statuses</option>
              {statuses.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <span className="text-xs text-muted-foreground">
            Showing {filtered.length} of {services.length}
          </span>
        </div>
        {filtered.map((service) => (
          <div
            key={service.id}
            className="flex flex-wrap items-center gap-2 text-sm"
          >
            <Dot tone={serviceHealthTone(service)} />
            <span className="font-medium">{service.id}</span>
            <span
              className={`ml-auto text-[13px] ${toneText[serviceHealthTone(service)]}`}
            >
              {serviceHealthLabel(service)}
            </span>
            <span className="basis-full pl-4 text-[13px] text-muted-foreground">
              {service.observed_at
                ? `Observed ${formatTimestamp(service.observed_at)}`
                : 'No observation timestamp'}
              {service.definition_state ? ` · ${service.definition_state}` : ''}
              {service.runtime_state && service.runtime_state !== 'unknown'
                ? ` · ${service.runtime_state}`
                : ''}
            </span>
          </div>
        ))}
        {!filtered.length ? (
          <p className="m-0 text-sm text-muted-foreground">
            {services.length
              ? 'No services match both filters.'
              : 'No service observations available.'}
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}

function formatTimestamp(value: string) {
  return (
    new Intl.DateTimeFormat('en-GB', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'UTC',
    }).format(new Date(value)) + ' UTC'
  )
}
