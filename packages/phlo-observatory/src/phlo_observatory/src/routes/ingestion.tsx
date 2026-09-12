/**
 * /ingestion route. Operational queue joining datasets with their pipelines,
 * surfacing candidates and freshness/readiness failures.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { AlertCircle, CheckCircle2, Import, Table2 } from 'lucide-react'
import { useMemo } from 'react'

import {
  getObservatoryDatasetRecords,
  getObservatoryPipelineRecords,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { useLiveResource } from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import { EmptyBlock } from '@/components/observatory/states'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { HealthDot } from '@/components/observatory/status'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { SectionCard } from '@/components/observatory/section'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

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
    <Page>
      <PageHeader
        actions={
          <Link
            className={cn(buttonVariants({ size: 'sm' }))}
            to="/workflows/new"
          >
            <Import className="size-3.5" />
            New ingestion workflow
          </Link>
        }
        description="Source onboarding, candidate review, dataset readiness, and pipeline freshness in one operational queue."
        title="Ingestion"
      />
      <StatGrid>
        <StatCard
          icon={<CheckCircle2 className="size-3.5" />}
          label="Datasets"
          value={loading ? '—' : rows.length}
        />
        <StatCard
          icon={<Table2 className="size-3.5" />}
          label="Queryable tables"
          value={loading ? '—' : (tables.data?.length ?? 0)}
        />
        <StatCard
          icon={<Import className="size-3.5" />}
          label="Candidates"
          state={candidates > 0 ? 'warning' : 'ok'}
          value={loading ? '—' : candidates}
        />
        <StatCard
          icon={<AlertCircle className="size-3.5" />}
          label="Blocked"
          state={blocked > 0 ? 'error' : 'ok'}
          value={loading ? '—' : blocked}
        />
      </StatGrid>
      <SplitView
        inspector={
          <>
            <InspectorSection label="From source to governed dataset">
              <p className="text-muted-foreground text-xs/relaxed">
                Candidate tables require ownership and classification before
                promotion. Published datasets require fresh pipeline and quality
                evidence.
              </p>
            </InspectorSection>
            <InspectorSection label="Queue shortcuts">
              <div className="divide-y divide-border border-y">
                <Link
                  className="hover:bg-accent/50 flex items-center justify-between gap-2 py-2 text-xs transition-colors"
                  to="/datasets"
                >
                  <span className="text-foreground">Review candidates</span>
                  <span className="text-muted-foreground font-mono text-[10px]">
                    {candidates} waiting
                  </span>
                </Link>
                <Link
                  className="hover:bg-accent/50 flex items-center justify-between gap-2 py-2 text-xs transition-colors"
                  to="/pipelines"
                >
                  <span className="text-foreground">Inspect freshness</span>
                  <span className="text-muted-foreground font-mono text-[10px]">
                    {blocked} blocked
                  </span>
                </Link>
                <Link
                  className="hover:bg-accent/50 flex items-center justify-between gap-2 py-2 text-xs transition-colors"
                  to="/workflows/new"
                >
                  <span className="text-foreground">Create workflow</span>
                  <span className="text-muted-foreground font-mono text-[10px]">
                    ingestion
                  </span>
                </Link>
              </div>
            </InspectorSection>
          </>
        }
        list={
          <SectionCard className="ring-0" title="Ingestion queue">
            <div className="text-muted-foreground grid grid-cols-[auto_minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
              <span />
              <span>Dataset</span>
              <span>Pipeline state</span>
              <span>Next action</span>
            </div>
            <div className="divide-y divide-border">
              {rows.map(({ dataset, pipeline, sources }) => (
                <div
                  className="grid grid-cols-[auto_minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)] items-center gap-3 px-3 py-2"
                  key={dataset.id}
                >
                  <HealthDot state={dataset.readiness_state} />
                  <span className="min-w-0">
                    <Link
                      className="text-foreground hover:text-primary block truncate text-xs font-medium"
                      params={{ datasetId: dataset.id }}
                      to="/datasets/$datasetId"
                    >
                      {dataset.name}
                    </Link>
                    <span className="text-muted-foreground block truncate font-mono text-[10px]">
                      {dataset.candidate
                        ? 'candidate'
                        : dataset.publication_state}
                      {sources.length ? ` · ${sources.join(', ')}` : ''}
                    </span>
                  </span>
                  <span className="min-w-0">
                    <span className="text-foreground block truncate text-[11px]">
                      {pipeline?.freshness_state ?? 'not observed'}
                    </span>
                    <span className="text-muted-foreground block truncate font-mono text-[10px]">
                      {pipeline?.freshness_at ?? 'no freshness timestamp'}
                    </span>
                  </span>
                  <span className="min-w-0">
                    <span className="text-foreground block truncate text-[11px]">
                      {nextAction(
                        dataset.candidate,
                        dataset.readiness_state,
                        pipeline?.freshness_state,
                      )}
                    </span>
                    <span className="text-muted-foreground block truncate font-mono text-[10px]">
                      {pipeline?.actions.length
                        ? `${pipeline.actions.length} supported actions`
                        : 'open workflow or dataset'}
                    </span>
                  </span>
                </div>
              ))}
              {!loading && !rows.length && (
                <EmptyBlock
                  description="Create an ingestion workflow to establish source, table, and dataset evidence."
                  title="No ingestion resources found"
                />
              )}
            </div>
          </SectionCard>
        }
      />
    </Page>
  )
}

function nextAction(
  candidate: boolean,
  readiness: string,
  freshness?: string,
): string {
  if (candidate) return 'Claim, classify, then promote'
  if (readiness === 'error') return 'Resolve dataset readiness blocker'
  if (freshness === 'error') return 'Recover failed pipeline stage'
  if (freshness === 'warning') return 'Review freshness warning'
  return 'Monitor source and freshness evidence'
}
