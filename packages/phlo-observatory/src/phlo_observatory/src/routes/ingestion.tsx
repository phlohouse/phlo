/** V1-backed operational entry points for assets and pipeline jobs. */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Database, Workflow } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import {
  environmentChangeEvent,
  selectedEnvironment,
} from '@/observatory/api/environment'
import { getV1PipelineSnapshot } from '@/observatory/api/pipelinesV1'
import { getV1AssetsPage } from '@/observatory/api/tablesV1'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/ingestion')({ component: Ingestion })

export function Ingestion() {
  const [environment, setEnvironment] = useState(selectedEnvironment)

  useEffect(() => {
    const update = () => setEnvironment(selectedEnvironment())
    window.addEventListener(environmentChangeEvent(), update)
    return () => window.removeEventListener(environmentChangeEvent(), update)
  }, [])

  const loadAssets = useCallback(
    () =>
      environment
        ? getV1AssetsPage({ data: { environment, cursor: null } }).then(
            (result) =>
              result.kind === 'available'
                ? {
                    data: result.data.items,
                    error:
                      result.data.next_cursor === null
                        ? null
                        : 'Only the first asset page is shown.',
                  }
                : { data: null, error: result.message },
          )
        : Promise.resolve({
            data: null,
            error: 'Select prod or staging to view environment-scoped assets.',
          }),
    [environment],
  )
  const assets = useLiveResource(
    loadAssets,
    60_000,
    `observatory:ingestion:assets:${environment ?? 'unselected'}`,
  )
  const loadPipelines = useCallback(
    () =>
      environment
        ? getV1PipelineSnapshot({ data: { environment } }).then((result) => ({
            data: result.data === null ? null : [result.data],
            error: result.error,
          }))
        : Promise.resolve({
            data: null,
            error: 'Select prod or staging to view environment-scoped jobs.',
          }),
    [environment],
  )
  const pipelines = useLiveResource(
    loadPipelines,
    60_000,
    `observatory:ingestion:pipelines:${environment ?? 'unselected'}`,
  )

  return (
    <ObservatoryPage
      kicker="Deliver"
      title="Ingestion"
      description="Environment-scoped assets and job definitions reported by the Phlo API. Governed Dataset readiness and source onboarding are not available without a Dataset contract."
    >
      <section className="phlo-observatory-command phlo-observatory-local-index-shell">
        <div className="phlo-observatory-command-primary">
          <div className="phlo-observatory-command-strip phlo-observatory-ingestion-summary">
            <IngestionMetric
              icon={<Database className="size-4" />}
              label="Assets in first page"
              value={metricValue(
                assets.isLoading,
                assets.data?.length,
                assets.error,
              )}
              error={assets.error}
            />
            <IngestionMetric
              icon={<Workflow className="size-4" />}
              label="Jobs"
              value={metricValue(
                pipelines.isLoading,
                pipelines.data?.[0]?.jobs.items.length,
                pipelines.error,
              )}
              error={pipelines.error}
            />
            <IngestionMetric
              icon={<Workflow className="size-4" />}
              label="Schedules"
              value={metricValue(
                pipelines.isLoading,
                pipelines.data?.[0]?.schedules.items.length,
                pipelines.error,
              )}
              error={pipelines.error}
            />
          </div>
          <div className="phlo-observatory-operation-empty">
            <div>
              <h2>Governed ingestion is unavailable</h2>
              <p>
                The v1 API exposes assets and jobs, but not governed Dataset
                publication, source references, or candidate readiness. No
                Dataset counts or states are inferred from assets.
              </p>
              <div className="phlo-observatory-detail-list">
                <Link className="phlo-observatory-mini-row" to="/tables">
                  <span>Inspect API assets</span>
                  <small>Table and preview contracts</small>
                </Link>
                <Link className="phlo-observatory-mini-row" to="/pipelines">
                  <span>Inspect jobs and schedules</span>
                  <small>Launch and schedule controls</small>
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>
    </ObservatoryPage>
  )
}

function metricValue(
  loading: boolean,
  count: number | undefined,
  error: string | null,
): number | string {
  if (loading) return '—'
  if (error) return 'Unavailable'
  return count ?? '—'
}

function IngestionMetric({
  icon,
  label,
  value,
  error,
}: {
  icon: React.ReactNode
  label: string
  value: number | string
  error: string | null
}) {
  return (
    <div className="phlo-observatory-command-metric" title={error ?? undefined}>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
