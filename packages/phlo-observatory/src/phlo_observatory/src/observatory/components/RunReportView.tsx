/**
 * Attempt-scoped run report page. Distinguishes loading, error (mapped from
 * API error codes), and empty reports, otherwise rendering the report's
 * lifecycle, stages, evidence, and artifact sections.
 */
import type {
  ObservatoryReportArtifact,
  ObservatoryReportCatalogChange,
  ObservatoryReportQuality,
  ObservatoryReportResource,
  ObservatoryReportStage,
  ObservatoryRunReport,
} from '@/observatory/api/types'
import type {
  ObservatoryRunReportErrorCode,
  ObservatoryRunReportResult,
} from '@/observatory/api/resources'
import type { ReactNode } from 'react'
import { Page, PageHeader } from '@/components/observatory/page'
import { SectionCard } from '@/components/observatory/section'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { Badge } from '@/components/ui/badge'

export interface RunReportRequest {
  projectId: string
  runId: string
  attempt: string
}

export function RunReportView({
  request,
  result,
}: {
  request: RunReportRequest
  result: ObservatoryRunReportResult | null
}) {
  const report = result?.data
  return (
    <Page>
      <PageHeader
        actions={<Badge variant="secondary">Attempt {request.attempt}</Badge>}
        breadcrumb={[
          { label: 'Runs', to: '/runs' },
          { label: request.runId },
          { label: 'Report' },
        ]}
        description={`Attempt-scoped evidence for ${request.projectId} / ${request.runId}.`}
        title="Run report"
      />
      {!result ? (
        <ReportState
          title="Loading run report"
          detail="Reading the exact attempt-scoped evidence snapshot."
        />
      ) : result.error ? (
        <ReportState
          detail={result.error}
          title={errorTitle(result.errorCode)}
        />
      ) : report ? (
        <ReportContent report={report} />
      ) : (
        <ReportState
          detail="The API returned no report payload for this attempt."
          title="No report evidence"
        />
      )}
    </Page>
  )
}

function ReportState({ title, detail }: { title: string; detail: string }) {
  return (
    <section
      aria-busy={title.startsWith('Loading')}
      className="bg-card ring-foreground/10 flex flex-col items-center justify-center gap-1.5 px-6 py-14 text-center ring-1"
    >
      <h2 className="text-foreground text-sm font-semibold">{title}</h2>
      <p className="text-muted-foreground max-w-md text-xs/relaxed">{detail}</p>
    </section>
  )
}

function errorTitle(code?: ObservatoryRunReportErrorCode) {
  if (code === 'access_denied') return 'Access denied'
  if (code === 'not_found') return 'Run report not found'
  if (code === 'invalid_request') return 'Run report details are incomplete'
  return 'Run report unavailable'
}

function ReportContent({ report }: { report: ObservatoryRunReport }) {
  const run = report.lifecycle.run
  const empty =
    !run &&
    report.lifecycle.events.length === 0 &&
    report.stages.length === 0 &&
    report.inputs.length === 0 &&
    report.staging.length === 0 &&
    report.outputs.length === 0 &&
    report.lineage.length === 0 &&
    report.transformations.length === 0 &&
    report.quality.length === 0 &&
    report.iceberg_snapshots.length === 0 &&
    report.catalog_changes.length === 0 &&
    report.artifacts.length === 0 &&
    !report.terminal_outcome

  if (empty) {
    return (
      <SectionCard>
        <ReportState
          title="No attempt-scoped evidence recorded"
          detail="This report contains no lifecycle or evidence records for the requested attempt."
        />
        <Gaps gaps={report.gaps} />
      </SectionCard>
    )
  }

  return (
    <div className="ring-foreground/10 bg-card grid grid-cols-1 rounded-none ring-1 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="flex min-w-0 flex-col">
        <Summary report={report} />
        <ReportSection title="Lifecycle">
          {run ? (
            <RunFacts run={run} />
          ) : (
            <MissingEvidence text="No run header was recorded." />
          )}
          <EventList events={report.lifecycle.events} />
        </ReportSection>
        <ReportSection title="Stages and transformations">
          <StageList stages={report.stages} />
          {report.transformations.length > 0 && (
            <div>
              <MiniLabel>Transformations</MiniLabel>
              <StageList stages={report.transformations} />
            </div>
          )}
        </ReportSection>
        <ReportSection title="Inputs, staging, and outputs">
          <ResourceGroup label="Inputs" resources={report.inputs} />
          <ResourceGroup label="Staging" resources={report.staging} />
          <ResourceGroup label="Outputs" resources={report.outputs} />
        </ReportSection>
        <ReportSection title="Lineage">
          {report.lineage.length ? (
            <MiniList>
              {report.lineage.map((edge) => (
                <MiniRow
                  detail={`${edge.origin} · ${edge.derivation}`}
                  key={edge.lineage_edge_id}
                  title={`${edge.source} → ${edge.target}`}
                />
              ))}
            </MiniList>
          ) : (
            <MissingEvidence text="No lineage evidence was recorded." />
          )}
        </ReportSection>
        <ReportSection title="Quality">
          {report.quality.length ? (
            <QualityList quality={report.quality} />
          ) : (
            <MissingEvidence text="No quality evidence was recorded." />
          )}
        </ReportSection>
        <ReportSection title="Catalog / Nessie evidence">
          <CatalogChanges changes={report.catalog_changes} />
          <ResourceGroup
            label="Iceberg snapshots"
            resources={report.iceberg_snapshots}
          />
        </ReportSection>
        <ReportSection title="Artifacts">
          {report.artifacts.length ? (
            <ArtifactList artifacts={report.artifacts} />
          ) : (
            <MissingEvidence text="No artifacts were recorded." />
          )}
        </ReportSection>
      </div>
      <aside className="border-t p-3 xl:border-t-0 xl:border-l">
        <h3 className="text-muted-foreground mb-2 text-[10px] font-medium tracking-widest uppercase">
          Outcome and gaps
        </h3>
        <TerminalOutcome report={report} />
        <Gaps gaps={report.gaps} />
      </aside>
    </div>
  )
}

function Summary({ report }: { report: ObservatoryRunReport }) {
  return (
    <div className="grid grid-cols-2 gap-px border-b sm:grid-cols-4">
      <Metric label="Project" value={report.project_id} />
      <Metric label="Run" value={report.run_id} />
      <Metric label="Attempt" value={report.attempt} />
      <Metric label="Schema" value={report.schema_version} />
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="px-3 py-2">
      <div className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
        {label}
      </div>
      <div className="text-foreground truncate font-mono text-xs font-semibold">
        {value}
      </div>
    </div>
  )
}

function ReportSection({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="border-b last:border-b-0">
      <h3 className="text-muted-foreground border-b px-3 py-1.5 text-[10px] font-medium tracking-widest uppercase">
        {title}
      </h3>
      <div className="px-3 py-2">{children}</div>
    </section>
  )
}

function MiniLabel({ children }: { children: ReactNode }) {
  return (
    <h4 className="text-muted-foreground mt-2 mb-1 text-[10px] font-medium tracking-widest uppercase">
      {children}
    </h4>
  )
}

function MiniList({ children }: { children: ReactNode }) {
  return <div className="divide-border divide-y border-y">{children}</div>
}

function MiniRow({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex flex-col gap-0.5 py-2">
      <span className="text-foreground font-mono text-[11px]">{title}</span>
      <span className="text-muted-foreground font-mono text-[10px] break-all">
        {detail}
      </span>
    </div>
  )
}

function RunFacts({
  run,
}: {
  run: NonNullable<ObservatoryRunReport['lifecycle']['run']>
}) {
  return (
    <FactGrid>
      <Fact label="Pipeline" value={run.pipeline_name} />
      <Fact label="Provider run" value={run.provider_run_id} />
      <Fact label="Status" value={run.status} />
      <Fact label="Started" value={run.started_at} />
      <Fact label="Finished" value={run.finished_at} />
      <Fact label="Evidence completeness" value={run.evidence_completeness} />
      <Fact label="Failure summary" value={run.failure_summary} />
    </FactGrid>
  )
}

function EventList({
  events,
}: {
  events: ObservatoryRunReport['lifecycle']['events']
}) {
  return events.length ? (
    <MiniList>
      {events.map((event) => (
        <MiniRow
          detail={`${event.observed_at ?? 'not timestamped'} · sequence ${display(event.sequence)}`}
          key={event.event_id}
          title={`${event.event_type} · ${event.producer}`}
        />
      ))}
    </MiniList>
  ) : (
    <MissingEvidence text="No lifecycle events were recorded." />
  )
}

function StageList({ stages }: { stages: Array<ObservatoryReportStage> }) {
  return stages.length ? (
    <MiniList>
      {stages.map((stage) => (
        <MiniRow
          detail={`${stage.provider ?? 'provider not reported'} · ${stage.asset ?? 'asset not reported'} · ${stage.error_fingerprint ?? 'no error fingerprint'}`}
          key={stage.stage_id}
          title={`${stage.stage_id} · ${stage.stage_type} · ${stage.status}`}
        />
      ))}
    </MiniList>
  ) : (
    <MissingEvidence text="No stage evidence was recorded." />
  )
}

function ResourceGroup({
  label,
  resources,
}: {
  label: string
  resources: Array<ObservatoryReportResource>
}) {
  return (
    <div>
      <MiniLabel>{label}</MiniLabel>
      {resources.length ? (
        <MiniList>
          {resources.map((resource) => (
            <ResourceRow key={resource.resource_id} resource={resource} />
          ))}
        </MiniList>
      ) : (
        <MissingEvidence
          text={`No ${label.toLowerCase()} evidence was recorded.`}
        />
      )}
    </div>
  )
}

function ResourceRow({ resource }: { resource: ObservatoryReportResource }) {
  return (
    <MiniRow
      detail={`${
        resource.table_name ??
        resource.normalized_identity ??
        resource.uri ??
        'identity not reported'
      } · ${resource.record_count ?? 'records not reported'} records · ${resource.byte_count ?? 'bytes not reported'} bytes · snapshots ${display(resource.snapshot_before)} → ${display(resource.snapshot_after)}`}
      title={`${resource.resource_id} · ${resource.resource_kind}`}
    />
  )
}

function QualityList({
  quality,
}: {
  quality: Array<ObservatoryReportQuality>
}) {
  return (
    <MiniList>
      {quality.map((check) => (
        <MiniRow
          detail={`${check.asset ?? 'asset not reported'} · severity ${display(check.severity)} · evaluated ${display(check.evaluated_count)} · failed ${display(check.failed_count)}`}
          key={check.quality_result_id}
          title={`${check.check_id} · ${check.passed ? 'passed' : 'failed'}${check.blocking ? ' · blocking' : ''}`}
        />
      ))}
    </MiniList>
  )
}

function CatalogChanges({
  changes,
}: {
  changes: Array<ObservatoryReportCatalogChange>
}) {
  return changes.length ? (
    <MiniList>
      {changes.map((change) => (
        <MiniRow
          detail={`${change.catalog_ref ?? 'catalog not reported'} · ref ${display(change.content_key)} · commit ${display(change.commit_hash)} · outcome ${display(change.merge_outcome)} · snapshots ${display(change.snapshot_before)} → ${display(change.snapshot_after)}`}
          key={change.catalog_change_id}
          title={`${change.catalog_change_id} · ${change.operation}`}
        />
      ))}
    </MiniList>
  ) : (
    <MissingEvidence text="No catalog or Nessie changes were recorded." />
  )
}

function ArtifactList({
  artifacts,
}: {
  artifacts: Array<ObservatoryReportArtifact>
}) {
  return (
    <MiniList>
      {artifacts.map((artifact) => (
        <MiniRow
          detail={`${artifact.uri ?? 'URI not reported'} · ${artifact.content_type ?? 'content type not reported'} · checksum ${display(artifact.checksum)} · legal hold ${String(artifact.legal_hold)}`}
          key={artifact.artifact_id}
          title={`${artifact.artifact_id} · ${artifact.artifact_kind} · ${artifact.status}`}
        />
      ))}
    </MiniList>
  )
}

function TerminalOutcome({ report }: { report: ObservatoryRunReport }) {
  const outcome = report.terminal_outcome
  return (
    <div className="mb-3">
      {outcome ? (
        <FactGrid>
          <Fact label="Status" value={outcome.status} />
          <Fact label="Source" value={outcome.source} />
          <Fact label="Evidence" value={outcome.evidence_id} />
          <Fact label="Observed" value={outcome.observed_at} />
        </FactGrid>
      ) : (
        <MissingEvidence text="No terminal outcome was recorded; the result remains unknown." />
      )}
    </div>
  )
}

function Gaps({ gaps }: { gaps: ObservatoryRunReport['gaps'] }) {
  return (
    <div>
      <h3 className="text-muted-foreground mb-1.5 text-[10px] font-medium tracking-widest uppercase">
        Explicit gaps
      </h3>
      {gaps.length ? (
        <MiniList>
          {gaps.map((gap) => (
            <MiniRow
              detail={gap.reason}
              key={`${gap.field}:${gap.reason}`}
              title={`${gap.field} · ${gap.status}`}
            />
          ))}
        </MiniList>
      ) : (
        <MissingEvidence text="No explicit gaps were returned." />
      )}
    </div>
  )
}

function MissingEvidence({ text }: { text: string }) {
  return <p className="text-muted-foreground py-1.5 text-[11px]">{text}</p>
}

function display(value: unknown) {
  return value === null || value === undefined || value === ''
    ? 'not reported'
    : String(value)
}
