/** Defines the Observatory overview dashboard and service-health summary. */
import { createFileRoute, useRouter } from '@tanstack/react-router'
import { RefreshCwIcon } from 'lucide-react'
import { getOverview } from '@/lib/data/api/core'
import { PageBody, PageHeader } from '@/components/phlo/page'
import { KpiCard } from '@/components/phlo/kpi'
import { Dot, toneText } from '@/components/phlo/status'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export const Route = createFileRoute('/_app/')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getOverview({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Overview · phlo' }] }),
  component: OverviewPage,
})

function OverviewPage() {
  const { overview, services } = Route.useLoaderData()
  const router = useRouter()
  const state =
    services.length === 0
      ? 'unknown'
      : services.every((service) => service.status === 'healthy')
        ? 'healthy'
        : services.some(
              (service) =>
                service.status === 'unavailable' ||
                service.status === 'unhealthy',
            )
          ? 'unhealthy'
          : services.some((service) => service.status === 'degraded')
            ? 'degraded'
            : 'unknown'
  const runCount = Object.values(overview.run_status_counts).reduce(
    (sum, count) => sum + count,
    0,
  )
  const quality = overview.quality_checks.counts

  return (
    <>
      <PageHeader
        title={`Overview · ${overview.env}`}
        meta="Environment-scoped data from the Phlo API"
        actions={
          <Button variant="outline" onClick={() => void router.invalidate()}>
            <RefreshCwIcon /> Refresh
          </Button>
        }
      />
      <PageBody>
        <div className="flex items-center gap-3 rounded-xl border border-border-card bg-card px-4 py-3">
          <Dot tone={healthTone(state)} size="md" />
          <div className="flex min-w-0 flex-col">
            <span className={`font-medium ${toneText[healthTone(state)]}`}>
              Environment services: {state}
            </span>
            <span className="text-[13px] text-muted-foreground">
              {services.length
                ? `${services.length} services from ${overview.env}`
                : 'No service health observations are available.'}
            </span>
          </div>
        </div>

        <section
          aria-label="Live counts"
          className="grid grid-cols-2 gap-3 lg:grid-cols-5 lg:gap-4"
        >
          <KpiCard label="Assets" value={overview.asset_count} />
          <KpiCard
            label="Materialized assets"
            value={overview.materialized_asset_count}
          />
          <KpiCard
            label="Freshness · fresh"
            value={overview.freshness_counts.fresh}
            footer={`${overview.freshness_counts.stale} stale · ${overview.freshness_counts.unknown} unknown`}
          />
          <KpiCard
            label="Open incidents"
            value={overview.incident_counts.open ?? 0}
          />
          <KpiCard
            label="Recent runs"
            value={runCount}
            footer={
              overview.run_history_truncated
                ? 'Recent history is truncated at the API limit.'
                : 'Counts from the selected environment.'
            }
          />
        </section>

        <Card>
          <CardHeader>
            <CardTitle>Environment services</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {services.length ? (
              services.map((service) => (
                <div
                  key={service.id}
                  className="flex flex-wrap items-center gap-2 text-sm"
                >
                  <Dot tone={healthTone(service.status)} />
                  <span className="font-medium">{service.id}</span>
                  <span
                    className={`ml-auto text-[13px] ${toneText[healthTone(service.status)]}`}
                  >
                    {service.status}
                  </span>
                  <span className="basis-full pl-4 text-[13px] text-muted-foreground">
                    {service.observed_at
                      ? `Observed ${formatTimestamp(service.observed_at)}`
                      : 'No observation timestamp'}
                    {service.response_time_seconds === null
                      ? ''
                      : ` · ${service.response_time_seconds.toFixed(3)} s`}
                  </span>
                </div>
              ))
            ) : (
              <p className="m-0 text-sm text-muted-foreground">
                The API returned no service observations.
              </p>
            )}
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Recent run status counts</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {Object.entries(overview.run_status_counts).length ? (
                Object.entries(overview.run_status_counts).map(
                  ([status, count]) => (
                    <div
                      key={status}
                      className="flex items-center gap-2 text-sm"
                    >
                      <Dot
                        tone={
                          status === 'SUCCESS'
                            ? 'ok'
                            : status === 'FAILURE'
                              ? 'bad'
                              : 'neutral'
                        }
                      />
                      <span>{status}</span>
                      <span className="ml-auto font-mono">{count}</span>
                    </div>
                  ),
                )
              ) : (
                <p className="m-0 text-sm text-muted-foreground">
                  The API returned no run status counts.
                </p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Quality checks</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              {quality ? (
                <>
                  <div>
                    {quality.passing} passing · {quality.total} total ·{' '}
                    {quality.unevaluated} unevaluated
                  </div>
                  <div className="text-[13px] text-muted-foreground">
                    {overview.quality_checks.failing_assets?.length
                      ? `${overview.quality_checks.failing_assets.length} assets have failing checks.`
                      : 'No failing assets were reported.'}
                  </div>
                </>
              ) : (
                <p className="m-0 text-muted-foreground">
                  Quality status is unknown
                  {overview.quality_checks.reason
                    ? `: ${overview.quality_checks.reason.replaceAll('_', ' ')}.`
                    : '.'}
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        <p className="m-0 text-xs text-muted-foreground">
          This overview uses the environment-scoped /api/v1 contract.
          {overview.latest_materialization_at
            ? ` Latest verified materialization: ${formatTimestamp(overview.latest_materialization_at)}.`
            : ' No verified materialization timestamp was reported.'}
        </p>
      </PageBody>
    </>
  )
}

function healthTone(status: string): 'ok' | 'warn' | 'bad' | 'neutral' {
  if (status === 'healthy') return 'ok'
  if (status === 'degraded') return 'warn'
  if (status === 'unhealthy' || status === 'unavailable') return 'bad'
  return 'neutral'
}

function formatTimestamp(value: string): string {
  return (
    new Intl.DateTimeFormat('en-GB', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'UTC',
    }).format(new Date(value)) + ' UTC'
  )
}
