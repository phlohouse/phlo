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
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

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
    <ObservatoryPage
      action={
        <span className="phlo-observatory-pill">Attempt {request.attempt}</span>
      }
      description={`Attempt-scoped evidence for ${request.projectId} / ${request.runId}.`}
      kicker="Run report"
      title="Run report"
    >
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
    </ObservatoryPage>
  )
}

function ReportState({ title, detail }: { title: string; detail: string }) {
  return (
    <section
      aria-busy={title.startsWith('Loading')}
      className="phlo-observatory-empty-state"
    >
      <h2>{title}</h2>
      <p>{detail}</p>
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
      <section className="phlo-observatory-command">
        <div className="phlo-observatory-command-primary phlo-observatory-panel">
          <ReportState
            title="No attempt-scoped evidence recorded"
            detail="This report contains no lifecycle or evidence records for the requested attempt."
          />
          <Gaps gaps={report.gaps} />
        </div>
      </section>
    )
  }

  return (
    <section className="phlo-observatory-command phlo-observatory-surface-grid">
      <div className="phlo-observatory-command-primary">
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
            <div className="phlo-observatory-detail-list">
              <div className="phlo-observatory-inspector-label">
                Transformations
              </div>
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
            <div className="phlo-observatory-detail-list">
              {report.lineage.map((edge) => (
                <div
                  className="phlo-observatory-mini-row"
                  key={edge.lineage_edge_id}
                >
                  <span>
                    {edge.source} → {edge.target}
                  </span>
                  <small>
                    {edge.origin} · {edge.derivation}
                  </small>
                </div>
              ))}
            </div>
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
      <aside className="phlo-observatory-inspector">
        <div className="phlo-observatory-inspector-label">Outcome and gaps</div>
        <TerminalOutcome report={report} />
        <Gaps gaps={report.gaps} />
      </aside>
    </section>
  )
}

function Summary({ report }: { report: ObservatoryRunReport }) {
  return (
    <div className="phlo-observatory-command-strip">
      <Metric label="Project" value={report.project_id} />
      <Metric label="Run" value={report.run_id} />
      <Metric label="Attempt" value={report.attempt} />
      <Metric label="Schema" value={report.schema_version} />
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="phlo-observatory-command-metric">
      <span>{label}</span>
      <strong>{value}</strong>
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
    <section className="phlo-observatory-panel">
      <div className="phlo-observatory-browser-toolbar">
        <span>{title}</span>
      </div>
      {children}
    </section>
  )
}

function RunFacts({
  run,
}: {
  run: NonNullable<ObservatoryRunReport['lifecycle']['run']>
}) {
  return (
    <dl className="phlo-observatory-facts">
      <Fact label="Pipeline" value={run.pipeline_name} />
      <Fact label="Provider run" value={run.provider_run_id} />
      <Fact label="Status" value={run.status} />
      <Fact label="Started" value={run.started_at} />
      <Fact label="Finished" value={run.finished_at} />
      <Fact label="Evidence completeness" value={run.evidence_completeness} />
      <Fact label="Failure summary" value={run.failure_summary} />
    </dl>
  )
}

function EventList({
  events,
}: {
  events: ObservatoryRunReport['lifecycle']['events']
}) {
  return events.length ? (
    <div className="phlo-observatory-detail-list">
      {events.map((event) => (
        <div className="phlo-observatory-mini-row" key={event.event_id}>
          <span>
            {event.event_type} · {event.producer}
          </span>
          <small>
            {event.observed_at ?? 'not timestamped'} · sequence{' '}
            {display(event.sequence)}
          </small>
        </div>
      ))}
    </div>
  ) : (
    <MissingEvidence text="No lifecycle events were recorded." />
  )
}

function StageList({ stages }: { stages: Array<ObservatoryReportStage> }) {
  return stages.length ? (
    <div className="phlo-observatory-detail-list">
      {stages.map((stage) => (
        <div className="phlo-observatory-mini-row" key={stage.stage_id}>
          <span>
            {stage.stage_id} · {stage.stage_type} · {stage.status}
          </span>
          <small>
            {stage.provider ?? 'provider not reported'} ·{' '}
            {stage.asset ?? 'asset not reported'} ·{' '}
            {stage.error_fingerprint ?? 'no error fingerprint'}
          </small>
        </div>
      ))}
    </div>
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
    <div className="phlo-observatory-detail-list">
      <div className="phlo-observatory-inspector-label">{label}</div>
      {resources.length ? (
        resources.map((resource) => (
          <ResourceRow key={resource.resource_id} resource={resource} />
        ))
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
    <div className="phlo-observatory-mini-row">
      <span>
        {resource.resource_id} · {resource.resource_kind}
      </span>
      <small>
        {resource.table_name ??
          resource.normalized_identity ??
          resource.uri ??
          'identity not reported'}{' '}
        · {resource.record_count ?? 'records not reported'} records ·{' '}
        {resource.byte_count ?? 'bytes not reported'} bytes · snapshots{' '}
        {display(resource.snapshot_before)} → {display(resource.snapshot_after)}
      </small>
    </div>
  )
}

function QualityList({
  quality,
}: {
  quality: Array<ObservatoryReportQuality>
}) {
  return (
    <div className="phlo-observatory-detail-list">
      {quality.map((check) => (
        <div
          className="phlo-observatory-mini-row"
          key={check.quality_result_id}
        >
          <span>
            {check.check_id} · {check.passed ? 'passed' : 'failed'}
            {check.blocking ? ' · blocking' : ''}
          </span>
          <small>
            {check.asset ?? 'asset not reported'} · severity{' '}
            {display(check.severity)} · evaluated{' '}
            {display(check.evaluated_count)} · failed{' '}
            {display(check.failed_count)}
          </small>
        </div>
      ))}
    </div>
  )
}

function CatalogChanges({
  changes,
}: {
  changes: Array<ObservatoryReportCatalogChange>
}) {
  return changes.length ? (
    <div className="phlo-observatory-detail-list">
      {changes.map((change) => (
        <div
          className="phlo-observatory-mini-row"
          key={change.catalog_change_id}
        >
          <span>
            {change.catalog_change_id} · {change.operation}
          </span>
          <small>
            {change.catalog_ref ?? 'catalog not reported'} · ref{' '}
            {display(change.content_key)} · commit {display(change.commit_hash)}{' '}
            · outcome {display(change.merge_outcome)} · snapshots{' '}
            {display(change.snapshot_before)} → {display(change.snapshot_after)}
          </small>
        </div>
      ))}
    </div>
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
    <div className="phlo-observatory-detail-list">
      {artifacts.map((artifact) => (
        <div className="phlo-observatory-mini-row" key={artifact.artifact_id}>
          <span>
            {artifact.artifact_id} · {artifact.artifact_kind} ·{' '}
            {artifact.status}
          </span>
          <small>
            {artifact.uri ?? 'URI not reported'} ·{' '}
            {artifact.content_type ?? 'content type not reported'} · checksum{' '}
            {display(artifact.checksum)} · legal hold{' '}
            {String(artifact.legal_hold)}
          </small>
        </div>
      ))}
    </div>
  )
}

function TerminalOutcome({ report }: { report: ObservatoryRunReport }) {
  const outcome = report.terminal_outcome
  return (
    <div className="phlo-observatory-detail-list">
      {outcome ? (
        <>
          <Fact label="Status" value={outcome.status} />
          <Fact label="Source" value={outcome.source} />
          <Fact label="Evidence" value={outcome.evidence_id} />
          <Fact label="Observed" value={outcome.observed_at} />
        </>
      ) : (
        <MissingEvidence text="No terminal outcome was recorded; the result remains unknown." />
      )}
    </div>
  )
}

function Gaps({ gaps }: { gaps: ObservatoryRunReport['gaps'] }) {
  return (
    <div className="phlo-observatory-detail-list">
      <div className="phlo-observatory-inspector-label">Explicit gaps</div>
      {gaps.length ? (
        gaps.map((gap) => (
          <div
            className="phlo-observatory-mini-row"
            key={`${gap.field}:${gap.reason}`}
          >
            <span>
              {gap.field} · {gap.status}
            </span>
            <small>{gap.reason}</small>
          </div>
        ))
      ) : (
        <MissingEvidence text="No explicit gaps were returned." />
      )}
    </div>
  )
}

function Fact({
  label,
  value,
}: {
  label: string
  value?: string | number | null
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{display(value)}</dd>
    </div>
  )
}

function MissingEvidence({ text }: { text: string }) {
  return <p className="phlo-observatory-panel-footer">{text}</p>
}

function display(value: unknown) {
  return value === null || value === undefined || value === ''
    ? 'not reported'
    : String(value)
}
