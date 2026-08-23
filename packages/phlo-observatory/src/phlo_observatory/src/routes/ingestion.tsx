/**
 * /ingestion route. Operational queue joining datasets with their pipelines,
 * surfacing candidates and freshness/readiness failures.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { AlertCircle, CheckCircle2, Import, Workflow } from 'lucide-react'
import { useMemo } from 'react'
import type { ReactNode } from 'react'

import {
  getObservatoryDatasetRecords,
  getObservatoryPipelineRecords,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/ingestion')({ component: Ingestion })

export function Ingestion() {
  const datasets = useLiveResource(
    getObservatoryDatasetRecords,
    60_000,
    'observatory:datasets',
  )
  const tables = useLiveResource(
    getObservatoryTableRecords,
    60_000,
    'observatory:tables',
  )
  const pipelines = useLiveResource(
    getObservatoryPipelineRecords,
    60_000,
    'observatory:pipelines',
  )
  const rows = useMemo(
    () =>
      (datasets.data ?? []).map((dataset) => {
        const pipeline = (pipelines.data ?? []).find(
          (item) => item.dataset?.id === dataset.id,
        )
        return {
          dataset,
          pipeline,
          sources: dataset.source_refs.map(
            (source) => source.label || source.id,
          ),
        }
      }),
    [datasets.data, pipelines.data],
  )
  const candidates = rows.filter((row) => row.dataset.candidate).length
  const blocked = rows.filter(
    (row) =>
      row.pipeline?.freshness_state === 'error' ||
      row.dataset.readiness_state === 'error',
  ).length
  const loading = datasets.isLoading || tables.isLoading || pipelines.isLoading

  return (
    <ObservatoryPage
      kicker="Deliver"
      title="Ingestion"
      description="Source onboarding, candidate review, Dataset readiness, and pipeline freshness in one operational queue."
      action={
        <Link className="phlo-observatory-map-action" to="/workflows/new">
          <Import className="size-3.5" />
          New ingestion workflow
        </Link>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-local-index-shell">
        <div className="phlo-observatory-command-primary">
          <div className="phlo-observatory-command-strip phlo-observatory-ingestion-summary">
            <IngestionMetric
              icon={<CheckCircle2 className="size-4" />}
              label="Datasets"
              value={loading ? '—' : rows.length}
            />
            <IngestionMetric
              icon={<Workflow className="size-4" />}
              label="Queryable tables"
              value={loading ? '—' : (tables.data?.length ?? 0)}
            />
            <IngestionMetric
              icon={<Import className="size-4" />}
              label="Candidates"
              value={loading ? '—' : candidates}
            />
            <IngestionMetric
              icon={<AlertCircle className="size-4" />}
              label="Blocked"
              value={loading ? '—' : blocked}
            />
          </div>
          <div className="phlo-observatory-ingestion-head">
            <span>Dataset</span>
            <span>Source evidence</span>
            <span>Pipeline state</span>
            <span>Next action</span>
          </div>
          {rows.map(({ dataset, pipeline, sources }) => (
            <div className="phlo-observatory-ingestion-row" key={dataset.id}>
              <span
                className="phlo-observatory-dot"
                data-state={dataset.readiness_state}
              />
              <span>
                <Link
                  params={{ datasetId: dataset.id }}
                  to="/datasets/$datasetId"
                >
                  <strong>{dataset.name}</strong>
                </Link>
                <small>
                  {dataset.candidate ? 'candidate' : dataset.publication_state}
                </small>
              </span>
              <span>
                {sources.join(', ') || 'No source reference'}
                <small>
                  {dataset.source_refs.length
                    ? 'Dataset read model'
                    : 'source evidence missing'}
                </small>
              </span>
              <span>
                {pipeline?.freshness_state ?? 'not observed'}
                <small>
                  {pipeline?.freshness_at ?? 'No freshness timestamp'}
                </small>
              </span>
              <span>
                {nextAction(
                  dataset.candidate,
                  dataset.readiness_state,
                  pipeline?.freshness_state,
                )}
                <small>
                  {pipeline?.actions.length
                    ? `${pipeline.actions.length} supported actions`
                    : 'Open workflow or Dataset'}
                </small>
              </span>
            </div>
          ))}
          {!loading && !rows.length && (
            <div className="phlo-observatory-operation-empty">
              <div>
                <h2>No ingestion resources found</h2>
                <p>
                  Create an ingestion workflow to establish source, table, and
                  Dataset evidence.
                </p>
              </div>
            </div>
          )}
        </div>
        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">
            Ingestion workflow
          </div>
          <h2>From source to governed Dataset</h2>
          <p>
            Candidate tables require ownership and classification before
            promotion. Published Datasets require fresh pipeline and quality
            evidence.
          </p>
          <div className="phlo-observatory-detail-list">
            <Link className="phlo-observatory-mini-row" to="/datasets">
              <span>Review candidates</span>
              <small>{candidates} waiting</small>
            </Link>
            <Link className="phlo-observatory-mini-row" to="/pipelines">
              <span>Inspect freshness</span>
              <small>{blocked} blocked</small>
            </Link>
            <Link className="phlo-observatory-mini-row" to="/workflows/new">
              <span>Create workflow</span>
              <small>Generate ingestion files</small>
            </Link>
          </div>
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function IngestionMetric({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: number | string
}) {
  return (
    <div className="phlo-observatory-command-metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function nextAction(
  candidate: boolean,
  readiness: string,
  freshness?: string,
): string {
  if (candidate) return 'Claim, classify, then promote'
  if (readiness === 'error') return 'Resolve Dataset readiness blocker'
  if (freshness === 'error') return 'Recover failed pipeline stage'
  if (freshness === 'warning') return 'Review freshness warning'
  return 'Monitor source and freshness evidence'
}
