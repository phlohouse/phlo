/**
 * Dataset profile route for /datasets/$datasetId. Fetches the profile
 * directly, bypassing the shared cache; refresh and actions invalidate
 * related cached resources.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  Activity,
  Boxes,
  CheckCircle2,
  Database,
  GitBranch,
  ListChecks,
  ShieldCheck,
  UploadCloud,
  UserPlus,
  UserRound,
  XCircle,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryDatasetProfile,
  ObservatoryResourceRef,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import {
  getObservatoryDatasetProfileDirect,
  runObservatoryActionDirect,
} from '@/observatory/api/resources'
import { profileProjection } from '@/observatory/api/datasetProjection'
import { DatasetProjectionPanel } from '@/observatory/components/DatasetProjectionPanel'
import { ActionButton } from '@/observatory/components/ActionButton'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { StatusBadge } from '@/observatory/components/StatusBadge'
import { invalidateCachedResources } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/datasets/$datasetId')({
  component: DatasetProfileRoute,
})

function DatasetProfileRoute() {
  const { datasetId } = Route.useParams()
  return <DatasetProfile datasetId={datasetId} />
}

export function DatasetProfile({ datasetId }: { datasetId: string }) {
  const [result, setResult] = useState<
    ObservatoryResourceResult<ObservatoryDatasetProfile>
  >({ data: null, error: null })

  function refreshProfile() {
    void getObservatoryDatasetProfileDirect({ datasetId }).then(setResult)
  }

  useEffect(() => {
    let cancelled = false
    void getObservatoryDatasetProfileDirect({ datasetId }).then((next) => {
      if (!cancelled) setResult(next)
    })
    return () => {
      cancelled = true
    }
  }, [datasetId])

  const profile = result.data
  const dataset = profile?.dataset

  return (
    <ObservatoryPage
      kicker="Dataset"
      title={dataset?.name ?? datasetId}
      description={
        dataset?.description ??
        'Dataset readiness cockpit for ownership, lineage, quality, publishing, and platform context.'
      }
      action={
        dataset ? (
          <span className="phlo-observatory-pill">
            {dataset.publication_state}
          </span>
        ) : null
      }
    >
      {profile ? (
        <ProfileContent onRefresh={refreshProfile} profile={profile} />
      ) : (
        <div className="phlo-observatory-empty-state">
          {result.error ?? 'Loading Dataset'}
        </div>
      )}
    </ObservatoryPage>
  )
}

function ProfileContent({
  onRefresh,
  profile,
}: {
  onRefresh: () => void
  profile: ObservatoryDatasetProfile
}) {
  const { dataset } = profile
  // The canonical projection the API embeds; the shared panel renders it
  // verbatim and the route never re-derives its facts.
  const canonical = profileProjection(profile)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const onAction = (actionId: string) => {
    setActionMessage('Requesting workflow action...')
    void runObservatoryActionDirect({ actionId }).then((next) => {
      invalidateCachedResources([
        'observatory:datasets',
        'observatory:operations',
      ])
      setActionMessage(next.data?.message ?? next.error ?? 'Action requested')
      onRefresh()
    })
  }

  return (
    <section className="phlo-observatory-surface-grid">
      <div className="phlo-observatory-list-surface phlo-observatory-dataset-profile-surface">
        <div className="phlo-observatory-browser-toolbar">
          <span>
            <Boxes className="size-4" />
            Readiness cockpit
          </span>
          <StatusBadge
            label={dataset.readiness_state}
            state={dataset.readiness_state}
          />
        </div>
        <ReadinessCockpit profile={profile} />
        <DatasetDecisionStrip profile={profile} />
        <div className="phlo-observatory-dataset-summary">
          <SummaryMetric
            icon={<Database className="size-5" />}
            label="Tables"
            value={profile.tables.length}
            detail={profile.tables[0]?.namespace ?? 'bound sources'}
          />
          <SummaryMetric
            icon={<ListChecks className="size-5" />}
            label="Checks"
            value={profile.quality.length}
            detail={`${dataset.readiness_state} readiness`}
          />
          <SummaryMetric
            icon={<GitBranch className="size-5" />}
            label="Lineage"
            value={profile.upstream.length + profile.downstream.length}
            detail={`${profile.upstream.length} up · ${profile.downstream.length} down`}
          />
          <SummaryMetric
            icon={<ShieldCheck className="size-5" />}
            label="Classifications"
            value={dataset.classifications.length}
            detail={
              dataset.classifications.length
                ? dataset.classifications.join(', ')
                : 'none declared'
            }
          />
        </div>
        <DatasetEvidenceWorkbench onAction={onAction} profile={profile} />
        {actionMessage && (
          <div className="phlo-observatory-panel-footer">{actionMessage}</div>
        )}
      </div>

      <aside className="phlo-observatory-inspector">
        <div className="phlo-observatory-inspector-label">Overview</div>
        <h2>{dataset.name}</h2>
        <p>{dataset.description ?? 'No description available.'}</p>
        <dl className="phlo-observatory-facts">
          <Fact
            icon={<UserRound className="size-3.5" />}
            label="Owner"
            value={dataset.owner ?? 'unassigned'}
          />
          <Fact label="Publication" value={dataset.publication_state} />
          <Fact label="Readiness" value={dataset.readiness_state} />
          <Fact
            label="Classification"
            value={
              dataset.classifications.length
                ? dataset.classifications.join(', ')
                : 'none'
            }
          />
        </dl>
        <div className="phlo-observatory-detail-list">
          <ReadinessInspectorRows onAction={onAction} profile={profile} />
          <ExactEvidenceRows profile={profile} />
          {canonical && <DatasetProjectionPanel projection={canonical} />}
          {profile.tables.map((table) => (
            <LinkedMiniRow
              detail={table.namespace ?? 'table'}
              href={`/tables?tableId=${encodeURIComponent(table.id)}`}
              key={table.id}
              label={table.name}
            />
          ))}
          {profile.tables.length === 0 && <EmptyRow label="No table binding" />}
        </div>
      </aside>
    </section>
  )
}

function DatasetDecisionStrip({
  profile,
}: {
  profile: ObservatoryDatasetProfile
}) {
  const { dataset } = profile
  const blocker = datasetBlocker(profile)
  const nextAction = datasetNextAction(profile)
  return (
    <div className="phlo-observatory-dataset-decision-strip">
      <DecisionFact
        label="Status"
        state={dataset.readiness_state}
        value={`${dataset.publication_state} · ${dataset.readiness_state}`}
      />
      <DecisionFact
        label="Owner"
        state={dataset.owner ? 'ok' : 'warning'}
        value={dataset.owner ?? 'unassigned'}
      />
      <DecisionFact
        label="Blocker"
        state={blocker.state}
        value={blocker.label}
      />
      <DecisionFact
        label="Next action"
        state={nextAction.state}
        value={nextAction.label}
      />
      <div className="phlo-observatory-dataset-evidence-rail">
        <EvidenceLink
          detail={firstQualityLabel(profile)}
          href={qualityHref(profile)}
          icon={<ListChecks className="size-3.5" />}
          label="Quality"
        />
        <EvidenceLink
          detail={operationLabel(profile)}
          href={operationHref(profile)}
          icon={<Activity className="size-3.5" />}
          label="Operations"
        />
        <EvidenceLink
          detail={lineageLabel(profile)}
          href={lineageHref(profile)}
          icon={<GitBranch className="size-3.5" />}
          label="Lineage"
        />
        <EvidenceLink
          detail={profile.publishing.policy_name}
          href={`/publishing?datasetId=${encodeURIComponent(profile.dataset.id)}`}
          icon={<UploadCloud className="size-3.5" />}
          label="Publishing"
        />
        <EvidenceLink
          detail={`${profile.governance.length} controls`}
          href={`/governance?datasetId=${encodeURIComponent(profile.dataset.id)}`}
          icon={<ShieldCheck className="size-3.5" />}
          label="Governance"
        />
      </div>
    </div>
  )
}

function DecisionFact({
  label,
  state,
  value,
}: {
  label: string
  state: string
  value: string
}) {
  return (
    <div className="phlo-observatory-dataset-decision-fact" data-state={state}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function EvidenceLink({
  detail,
  href,
  icon,
  label,
}: {
  detail: string
  href: string
  icon: ReactNode
  label: string
}) {
  return (
    <Link className="phlo-observatory-dataset-evidence-link" to={href}>
      <span>
        {icon}
        {label}
      </span>
      <small>{detail}</small>
    </Link>
  )
}

function DatasetEvidenceWorkbench({
  onAction,
  profile,
}: {
  onAction: (actionId: string) => void
  profile: ObservatoryDatasetProfile
}) {
  const lineage = [...profile.upstream, ...profile.downstream]
  const publishingIssues = datasetPublishingIssues(profile)
  return (
    <div className="phlo-observatory-dataset-evidence-workbench">
      <WorkflowPanel
        detail={
          publishingIssues.length
            ? `${publishingIssues.length} release issues`
            : 'no release issues'
        }
        state={
          profile.publishing.blockers.length
            ? 'error'
            : profile.publishing.state
        }
        title="Release decision"
      >
        <BlockerRows profile={profile} />
        <PublishingDecisionRows onAction={onAction} profile={profile} />
        {profile.dataset.candidate && (
          <CandidateWorkflowRows onAction={onAction} profile={profile} />
        )}
      </WorkflowPanel>
      <WorkflowPanel
        detail={`${profile.quality.length} checks · ${profile.governance.length} controls`}
        state={profile.dataset.readiness_state}
        title="Trust and controls"
      >
        <QualityRows profile={profile} />
        <GovernanceRows profile={profile} />
      </WorkflowPanel>
      <WorkflowPanel
        detail={
          profile.pipeline.last_run?.label ?? profile.pipeline.freshness_state
        }
        state={profile.pipeline.freshness_state}
        title="Pipeline evidence"
      >
        <OperationRows profile={profile} />
        <PipelineRows profile={profile} />
        <LogRows profile={profile} />
      </WorkflowPanel>
      <WorkflowPanel
        detail={`${profile.tables.length} tables · ${lineage.length} lineage refs`}
        state={profile.usage.access_activity.length ? 'ok' : 'unknown'}
        title="Bindings and adoption"
      >
        {profile.dataset.source_refs.map((ref) => (
          <LinkedMiniRow
            detail={resourceKindLabel(ref.kind)}
            href={resourceHref(ref)}
            key={`${ref.kind}:${ref.id}`}
            label={ref.label}
          />
        ))}
        <UsageRows profile={profile} />
        {lineage.length ? (
          lineage.map((ref) => (
            <LinkedMiniRow
              detail={
                profile.upstream.includes(ref) ? 'upstream' : 'downstream'
              }
              href={resourceHref(ref)}
              key={`${ref.kind}:${ref.id}`}
              label={ref.label}
            />
          ))
        ) : (
          <EmptyRow label="No lineage linked" />
        )}
      </WorkflowPanel>
    </div>
  )
}

function WorkflowPanel({
  children,
  detail,
  state,
  title,
}: {
  children: ReactNode
  detail: string
  state: string
  title: string
}) {
  return (
    <section
      className="phlo-observatory-dataset-workflow-panel"
      data-state={state}
    >
      <header>
        <div>
          <h3>{title}</h3>
          <p>{detail}</p>
        </div>
        <span className="phlo-observatory-dot" data-state={state} />
      </header>
      <div className="phlo-observatory-dataset-workflow-list">{children}</div>
    </section>
  )
}

function ReadinessCockpit({ profile }: { profile: ObservatoryDatasetProfile }) {
  const { dataset, publishing } = profile
  const blockingCheck = profile.quality.find(
    (check) => check.blocking && check.status !== 'passing',
  )
  const failedOperation = profile.operations.find(
    (operation) => operation.status === 'failed',
  )
  const governanceFailures = profile.governance.filter(
    (control) => control.status === 'fail',
  )
  const releaseIssues = datasetPublishingIssues(profile)
  const publicationAction =
    publishing.actions.find((action) => action.enabled) ?? null
  const publicationActionReason =
    publishing.actions.find((action) => !action.enabled)?.reason ??
    'No publication action is currently available.'
  const headline = publishing.blockers.length
    ? `${publishing.blockers.length} blocker${publishing.blockers.length === 1 ? '' : 's'} before release`
    : publishing.missing_evidence.length
      ? `${publishing.missing_evidence.length} evidence gap${publishing.missing_evidence.length === 1 ? '' : 's'} before release`
      : publishing.warnings.length
        ? `${publishing.warnings.length} warning${publishing.warnings.length === 1 ? '' : 's'} before release`
        : dataset.readiness_state === 'ok'
          ? 'Ready for internal publication'
          : 'Readiness needs review'

  return (
    <div
      className="phlo-observatory-dataset-cockpit"
      data-state={dataset.readiness_state}
    >
      <div className="phlo-observatory-dataset-cockpit-header">
        <div>
          <span className="phlo-observatory-dot-label">
            <span
              className="phlo-observatory-dot"
              data-state={
                dataset.readiness_state === 'error'
                  ? 'error'
                  : dataset.readiness_state
              }
            />
            {dataset.publication_state}
          </span>
          <h2>{headline}</h2>
          <p>
            {dataset.name} is governed by {publishing.policy_name}; publication
            is{' '}
            {publishing.internal_only ? 'internal only' : 'externally visible'}.
          </p>
        </div>
        <div className="phlo-observatory-dataset-cockpit-actions">
          {publicationAction ? (
            <span className="phlo-observatory-pill">
              {publicationAction.label}
            </span>
          ) : (
            <span className="phlo-observatory-pill">
              {publicationActionReason}
            </span>
          )}
        </div>
      </div>
      <div className="phlo-observatory-dataset-cockpit-grid">
        <CockpitCell
          detail={
            blockingCheck
              ? `${blockingCheck.status} · ${blockingCheck.severity ?? 'severity unset'}`
              : 'No blocking quality failure'
          }
          href={
            blockingCheck
              ? `/quality?checkId=${encodeURIComponent(blockingCheck.id)}`
              : undefined
          }
          icon={<ListChecks className="size-4" />}
          label="Trust"
          state={blockingCheck ? 'error' : 'ok'}
          title={blockingCheck?.name ?? 'Quality clear'}
        />
        <CockpitCell
          detail={
            governanceFailures.length
              ? governanceFailures.map((control) => control.label).join(', ')
              : 'Owner and controls are present'
          }
          href={`/governance?datasetId=${encodeURIComponent(dataset.id)}`}
          icon={<ShieldCheck className="size-4" />}
          label="Governance"
          state={governanceFailures.length ? 'error' : 'ok'}
          title={
            governanceFailures.length
              ? `${governanceFailures.length} failed controls`
              : 'Controls passing'
          }
        />
        <CockpitCell
          detail={
            failedOperation?.health.message ??
            profile.pipeline.freshness_at ??
            profile.pipeline.freshness_state
          }
          href={
            failedOperation
              ? `/operations?operationId=${encodeURIComponent(failedOperation.id)}`
              : profile.pipeline.last_run
                ? `/operations?operationId=${encodeURIComponent(profile.pipeline.last_run.id)}`
                : undefined
          }
          icon={<Activity className="size-4" />}
          label="Pipeline"
          state={failedOperation ? 'error' : profile.pipeline.freshness_state}
          title={
            failedOperation?.name ??
            profile.pipeline.last_run?.label ??
            'No run evidence'
          }
        />
        <CockpitCell
          detail={
            publishing.blockers[0] ??
            publishing.missing_evidence[0] ??
            publishing.warnings[0] ??
            'Policy has no active release issues'
          }
          href={`/publishing?datasetId=${encodeURIComponent(dataset.id)}`}
          icon={<UploadCloud className="size-4" />}
          label="Publishing"
          state={publishing.state}
          title={
            publishing.blockers.length
              ? 'Blocked'
              : releaseIssues.length
                ? 'Needs evidence'
                : 'Ready'
          }
        />
      </div>
    </div>
  )
}

function ExactEvidenceRows({
  profile,
}: {
  profile: ObservatoryDatasetProfile
}) {
  return (
    <>
      <LinkedMiniRow
        detail={firstQualityLabel(profile)}
        href={qualityHref(profile)}
        label="Quality evidence"
        state={profile.dataset.readiness_state}
      />
      <LinkedMiniRow
        detail={operationLabel(profile)}
        href={operationHref(profile)}
        label="Operations evidence"
        state={profile.pipeline.freshness_state}
      />
      <LinkedMiniRow
        detail={lineageLabel(profile)}
        href={lineageHref(profile)}
        label="Lineage evidence"
      />
      <LinkedMiniRow
        detail={profile.publishing.policy_name}
        href={`/publishing?datasetId=${encodeURIComponent(profile.dataset.id)}`}
        label="Publishing evidence"
        state={profile.publishing.state}
      />
      <LinkedMiniRow
        detail={`${profile.governance.length} controls`}
        href={`/governance?datasetId=${encodeURIComponent(profile.dataset.id)}`}
        label="Governance evidence"
        state={governanceEvidenceState(profile)}
      />
    </>
  )
}

function CockpitCell({
  detail,
  href,
  icon,
  label,
  state,
  title,
}: {
  detail: string | null | undefined
  href?: string
  icon: ReactNode
  label: string
  state: string
  title: string
}) {
  const content = (
    <>
      <span>
        {icon}
        {label}
      </span>
      <strong>{title}</strong>
      <small>{detail ?? 'No evidence linked'}</small>
    </>
  )
  if (!href) {
    return (
      <div className="phlo-observatory-dataset-cockpit-cell" data-state={state}>
        {content}
      </div>
    )
  }
  return (
    <Link
      className="phlo-observatory-dataset-cockpit-cell"
      data-state={state}
      to={href}
    >
      {content}
    </Link>
  )
}

function BlockerRows({ profile }: { profile: ObservatoryDatasetProfile }) {
  const blockers = profile.publishing.blockers
  const missingEvidence = profile.publishing.missing_evidence
  const warnings = profile.publishing.warnings
  if (
    blockers.length === 0 &&
    missingEvidence.length === 0 &&
    warnings.length === 0
  ) {
    return <EmptyRow label="Release controls clear" />
  }
  return (
    <>
      {blockers.map((blocker) => (
        <div
          className="phlo-observatory-mini-row"
          data-state="error"
          key={`blocker:${blocker}`}
        >
          <span>{blocker}</span>
          <small>blocker</small>
        </div>
      ))}
      {missingEvidence.map((item) => (
        <div
          className="phlo-observatory-mini-row"
          data-state="unknown"
          key={`missing:${item}`}
        >
          <span>{item}</span>
          <small>missing evidence</small>
        </div>
      ))}
      {warnings.map((warning) => (
        <div
          className="phlo-observatory-mini-row"
          data-state="warning"
          key={`warning:${warning}`}
        >
          <span>{warning}</span>
          <small>warning</small>
        </div>
      ))}
    </>
  )
}

function QualityRows({ profile }: { profile: ObservatoryDatasetProfile }) {
  if (!profile.quality.length)
    return <EmptyRow label="No quality checks configured" />
  return (
    <>
      {profile.quality.map((check) => (
        <LinkedMiniRow
          detail={`${check.status}${check.blocking ? ' · blocking' : ''}`}
          href={`/quality?checkId=${encodeURIComponent(check.id)}`}
          key={check.id}
          label={check.name}
          state={check.status === 'failing' ? 'error' : check.status}
        />
      ))}
    </>
  )
}

function GovernanceRows({ profile }: { profile: ObservatoryDatasetProfile }) {
  if (!profile.governance.length) {
    return <EmptyRow label="No governance controls configured" />
  }
  return (
    <>
      {profile.governance.map((control) => {
        const evidence = control.evidence.find(
          (item) => item.resource,
        )?.resource
        return evidence ? (
          <LinkedMiniRow
            detail={control.status.replace('_', ' ')}
            href={resourceHref(evidence)}
            key={control.id}
            label={control.label}
            state={controlStatusState(control.status)}
          />
        ) : (
          <div
            className="phlo-observatory-mini-row"
            data-state={controlStatusState(control.status)}
            key={control.id}
          >
            <span>{control.label}</span>
            <small>{control.message ?? control.status.replace('_', ' ')}</small>
          </div>
        )
      })}
    </>
  )
}

function OperationRows({ profile }: { profile: ObservatoryDatasetProfile }) {
  if (!profile.operations.length) {
    return <EmptyRow label="No operation history linked" />
  }
  return (
    <>
      {profile.operations.map((operation) => (
        <LinkedMiniRow
          detail={[
            operation.status,
            operation.completed_at
              ? formatDateTime(operation.completed_at)
              : null,
          ]
            .filter(Boolean)
            .join(' · ')}
          href={`/operations?operationId=${encodeURIComponent(operation.id)}`}
          key={operation.id}
          label={operation.name}
          state={operation.health.state}
        />
      ))}
    </>
  )
}

function LogRows({ profile }: { profile: ObservatoryDatasetProfile }) {
  if (!profile.logs.length) return <EmptyRow label="No linked log evidence" />
  return (
    <>
      {profile.logs.map((log) => (
        <LinkedMiniRow
          detail={[log.level, formatDateTime(log.timestamp)]
            .filter(Boolean)
            .join(' · ')}
          href={`/logs?logId=${encodeURIComponent(log.id)}`}
          key={log.id}
          label={log.message}
          state={
            log.level === 'error'
              ? 'error'
              : log.level === 'warning'
                ? 'warning'
                : 'ok'
          }
        />
      ))}
    </>
  )
}

function ReadinessInspectorRows({
  onAction,
  profile,
}: {
  onAction: (actionId: string) => void
  profile: ObservatoryDatasetProfile
}) {
  const publishing = profile.publishing
  const enabledPublicationAction = publishing.actions.find(
    (action) => action.enabled,
  )
  const pipelineAction = profile.pipeline.actions.find(
    (action) => action.enabled,
  )
  const releaseIssueCount = datasetPublishingIssues(profile).length
  return (
    <>
      <div className="phlo-observatory-mini-row" data-state={publishing.state}>
        <span>Publication policy</span>
        <small>{publishing.policy_name}</small>
      </div>
      <div
        className="phlo-observatory-mini-row"
        data-state={publishing.blockers.length ? 'error' : publishing.state}
      >
        <span>Release issues</span>
        <small>{releaseIssueCount}</small>
      </div>
      <div
        className="phlo-observatory-mini-row"
        data-state={publishing.missing_evidence.length ? 'unknown' : 'ok'}
      >
        <span>Missing evidence</span>
        <small>{publishing.missing_evidence.length}</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Next publish action</span>
        <small>
          {enabledPublicationAction?.label ??
            publishing.actions[0]?.reason ??
            'No publication action'}
        </small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Pipeline action</span>
        <small>
          {pipelineAction?.label ??
            profile.pipeline.actions[0]?.reason ??
            'none'}
        </small>
      </div>
      {publishing.actions.length > 0 && (
        <div className="phlo-observatory-action-row">
          {publishing.actions.map((action) => (
            <ActionButton
              action={{
                ...action,
                id: `dataset:${profile.dataset.id}:${action.id}`,
                kind: `dataset.${action.id}`,
                requires_confirmation: false,
                risk_level: action.id === 'retire' ? 'medium' : 'low',
                expected_evidence: [],
              }}
              key={action.id}
              onRun={onAction}
            />
          ))}
        </div>
      )}
    </>
  )
}

function PipelineRows({ profile }: { profile: ObservatoryDatasetProfile }) {
  const pipeline = profile.pipeline
  return (
    <>
      <LinkedMiniRow
        detail={pipeline.freshness_at ?? pipeline.freshness_state}
        href={
          pipeline.last_run
            ? `/operations?operationId=${encodeURIComponent(pipeline.last_run.id)}`
            : `/datasets/${encodeURIComponent(profile.dataset.id)}`
        }
        label="Freshness"
        state={pipeline.freshness_state}
      />
      {pipeline.last_run ? (
        <LinkedMiniRow
          detail="last run"
          href={`/operations?operationId=${encodeURIComponent(pipeline.last_run.id)}`}
          label={pipeline.last_run.label}
        />
      ) : (
        <EmptyRow label="No last run linked" />
      )}
      {pipeline.stages.map((stage) => (
        <LinkedMiniRow
          detail={stage.state}
          href={pipelineStageHref(profile, stage)}
          key={stage.id}
          label={stage.label}
          state={stage.state}
        />
      ))}
      {pipeline.actions.map((action) => (
        <div
          className="phlo-observatory-mini-row"
          data-state={action.enabled ? 'ok' : 'unknown'}
          key={action.id}
        >
          <span>{action.label}</span>
          <small>{action.enabled ? 'available' : action.reason}</small>
        </div>
      ))}
    </>
  )
}

function PublishingDecisionRows({
  onAction,
  profile,
}: {
  onAction: (actionId: string) => void
  profile: ObservatoryDatasetProfile
}) {
  const publishing = profile.publishing
  const enabledPublicationAction = publishing.actions.find(
    (action) => action.enabled,
  )
  return (
    <>
      <div className="phlo-observatory-mini-row" data-state={publishing.state}>
        <span>Publication policy</span>
        <small>{publishing.policy_name}</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Internal only</span>
        <small>{publishing.internal_only ? 'yes' : 'no'}</small>
      </div>
      <div
        className="phlo-observatory-mini-row"
        data-state={publishing.missing_evidence.length ? 'unknown' : 'ok'}
      >
        <span>Missing evidence</span>
        <small>{publishing.missing_evidence.length}</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Next publish action</span>
        <small>
          {enabledPublicationAction?.label ??
            publishing.actions[0]?.reason ??
            'No publication action'}
        </small>
      </div>
      <div className="phlo-observatory-action-row">
        {publishing.actions.map((action) => (
          <ActionButton
            action={{
              ...action,
              id: `dataset:${profile.dataset.id}:${action.id}`,
              kind: `dataset.${action.id}`,
              requires_confirmation: false,
              risk_level: action.id === 'retire' ? 'medium' : 'low',
              expected_evidence: [],
            }}
            key={action.id}
            onRun={onAction}
          />
        ))}
      </div>
    </>
  )
}

function datasetPublishingIssues(
  profile: ObservatoryDatasetProfile,
): Array<string> {
  return [
    ...profile.publishing.blockers,
    ...profile.publishing.missing_evidence,
    ...profile.publishing.warnings,
  ]
}

function datasetBlocker(profile: ObservatoryDatasetProfile): {
  label: string
  state: string
} {
  // Canonical-only: the blocker comes from the canonical
  // readiness verdict the profile embeds; nothing is re-inferred from
  // quality, operations, owner, or classification fields here.
  if (profile.publishing.blockers[0]) {
    return { label: profile.publishing.blockers[0], state: 'error' }
  }
  if (profile.publishing.missing_evidence[0]) {
    return { label: profile.publishing.missing_evidence[0], state: 'warning' }
  }
  if (profile.publishing.warnings[0]) {
    return { label: profile.publishing.warnings[0], state: 'warning' }
  }
  return { label: 'No active blocker', state: 'ok' }
}

function datasetNextAction(profile: ObservatoryDatasetProfile): {
  label: string
  state: string
} {
  const enabledPublicationAction = profile.publishing.actions.find(
    (action) => action.enabled,
  )
  const pipelineAction = profile.pipeline.actions.find(
    (action) => action.enabled,
  )
  if (profile.dataset.candidate)
    return { label: 'claim or promote', state: 'warning' }
  if (profile.publishing.blockers.length) {
    return { label: 'resolve release blockers', state: 'error' }
  }
  if (profile.publishing.missing_evidence.length) {
    return { label: 'collect publishing evidence', state: 'warning' }
  }
  if (profile.dataset.publication_state === 'published') {
    return { label: 'monitor evidence', state: profile.dataset.readiness_state }
  }
  if (enabledPublicationAction) {
    return { label: enabledPublicationAction.label, state: 'ok' }
  }
  if (pipelineAction) return { label: pipelineAction.label, state: 'warning' }
  return {
    label: profile.publishing.actions[0]?.reason ?? 'monitor evidence',
    state: profile.dataset.readiness_state,
  }
}

function CandidateWorkflowRows({
  onAction,
  profile,
}: {
  onAction: (actionId: string) => void
  profile: ObservatoryDatasetProfile
}) {
  const sourceId = profile.dataset.source_refs[0]?.id ?? profile.dataset.id
  return (
    <>
      <div className="phlo-observatory-mini-row">
        <span>Claim</span>
        <small>assign one accountable owner before promotion</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Promote</span>
        <small>turn the candidate into a governed Dataset</small>
      </div>
      <div className="phlo-observatory-inline-actions">
        <button
          onClick={() => onAction(`candidate:${sourceId}:claim`)}
          type="button"
        >
          <UserPlus className="size-3.5" />
          Claim
        </button>
        <button
          onClick={() => onAction(`candidate:${sourceId}:promote`)}
          type="button"
        >
          <CheckCircle2 className="size-3.5" />
          Promote
        </button>
        <button
          onClick={() => onAction(`candidate:${sourceId}:reject`)}
          type="button"
        >
          <XCircle className="size-3.5" />
          Reject
        </button>
      </div>
    </>
  )
}

function UsageRows({ profile }: { profile: ObservatoryDatasetProfile }) {
  const usage = profile.usage
  const gaps = [
    usage.access_activity.length ? null : 'access activity',
    usage.dependency_activity.length ? null : 'dependency activity',
    usage.consumer_adoption.length ? null : 'consumer adoption',
  ].filter((gap): gap is string => Boolean(gap))
  return (
    <>
      <div className="phlo-observatory-mini-row">
        <span>Access Activity</span>
        <small>{usage.access_activity.length}</small>
      </div>
      {usage.access_activity.slice(0, 4).map((activity) => (
        <div className="phlo-observatory-mini-row" key={activity.id}>
          <span>{activity.action}</span>
          <small>
            {activity.actor_label ?? 'access'} · {activity.count}
          </small>
        </div>
      ))}
      <div className="phlo-observatory-mini-row">
        <span>Dependency Activity</span>
        <small>{usage.dependency_activity.length}</small>
      </div>
      {usage.dependency_activity.slice(0, 4).map((activity) => (
        <LinkedMiniRow
          detail={activityKindLabel(activity.kind)}
          href={resourceHref(activity.source)}
          key={activity.id}
          label={activity.source.label}
        />
      ))}
      <div className="phlo-observatory-mini-row">
        <span>Consumer Adoption</span>
        <small>{usage.consumer_adoption.length}</small>
      </div>
      {usage.consumer_adoption.slice(0, 4).map((consumer) => (
        <div className="phlo-observatory-mini-row" key={consumer.id}>
          <span>{consumer.consumer}</span>
          <small>
            {consumer.kind} · {consumer.status}
          </small>
        </div>
      ))}
      <div className="phlo-observatory-mini-row">
        <span>Privacy</span>
        <small>{usage.privacy_policy.identity_detail.replace('_', ' ')}</small>
      </div>
      <div className="phlo-observatory-mini-row">
        <span>Telemetry gaps</span>
        <small>{gaps.length ? gaps.join(', ') : 'none'}</small>
      </div>
    </>
  )
}

function LinkedMiniRow({
  detail,
  href,
  label,
  state,
}: {
  detail: string | null | undefined
  href: string
  label: string
  state?: string | null
}) {
  return (
    <Link
      className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
      data-state={state ?? undefined}
      to={href}
    >
      <span>{label}</span>
      <small>{detail || 'open'}</small>
    </Link>
  )
}

function resourceHref(resource: ObservatoryResourceRef): string {
  if (resource.kind === 'dataset') {
    return `/datasets/${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'table') {
    return `/tables?tableId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'asset') {
    return `/lineage?assetId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'quality') {
    return `/quality?checkId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'operation') {
    return `/operations?operationId=${encodeURIComponent(resource.id)}`
  }
  return '/lineage'
}

function qualityHref(profile: ObservatoryDatasetProfile): string {
  const blocking = profile.quality.find(
    (check) => check.blocking && check.status !== 'passing',
  )
  const check = blocking ?? profile.quality[0]
  return check ? `/quality?checkId=${encodeURIComponent(check.id)}` : `/quality`
}

function firstQualityLabel(profile: ObservatoryDatasetProfile): string {
  const blocking = profile.quality.find(
    (check) => check.blocking && check.status !== 'passing',
  )
  const check = blocking ?? profile.quality[0]
  return check ? `${check.name} · ${check.status}` : 'no check linked'
}

function operationHref(profile: ObservatoryDatasetProfile): string {
  const operation =
    profile.operations.find((item) => item.status === 'failed') ??
    profile.pipeline.last_run
  return operation
    ? `/operations?operationId=${encodeURIComponent(operation.id)}`
    : '/operations'
}

function operationLabel(profile: ObservatoryDatasetProfile): string {
  const failedOperation = profile.operations.find(
    (item) => item.status === 'failed',
  )
  const operationLabel =
    failedOperation?.name ?? profile.pipeline.last_run?.label
  return operationLabel
    ? `${operationLabel} · ${profile.pipeline.freshness_state}`
    : profile.pipeline.freshness_state
}

function lineageHref(profile: ObservatoryDatasetProfile): string {
  const ref =
    profile.dataset.source_refs.find((item) => item.kind === 'asset') ??
    profile.upstream[0] ??
    profile.downstream[0]
  return ref ? resourceHref(ref) : '/lineage'
}

function lineageLabel(profile: ObservatoryDatasetProfile): string {
  const total = profile.upstream.length + profile.downstream.length
  if (total)
    return `${profile.upstream.length} up · ${profile.downstream.length} down`
  return profile.dataset.source_refs[0]?.label ?? 'no lineage linked'
}

function governanceEvidenceState(profile: ObservatoryDatasetProfile): string {
  if (profile.governance.some((control) => control.status === 'fail')) {
    return 'error'
  }
  if (
    profile.governance.some((control) =>
      ['warning', 'unknown'].includes(control.status),
    )
  ) {
    return 'warning'
  }
  return 'ok'
}

function resourceKindLabel(kind: string): string {
  if (kind === 'asset') return 'source binding'
  return kind.replace('_', ' ')
}

function activityKindLabel(kind: string): string {
  if (kind === 'asset_dependency') return 'lineage dependency'
  return resourceKindLabel(kind)
}

function pipelineStageHref(
  profile: ObservatoryDatasetProfile,
  stage: ObservatoryDatasetProfile['pipeline']['stages'][number],
): string {
  if (stage.resource) return resourceHref(stage.resource)
  if (stage.id === 'checks' && profile.quality[0]) {
    return `/quality?checkId=${encodeURIComponent(profile.quality[0].id)}`
  }
  if (stage.id === 'publish') {
    return `/publishing?datasetId=${encodeURIComponent(profile.dataset.id)}`
  }
  if (profile.pipeline.last_run) {
    return `/operations?operationId=${encodeURIComponent(profile.pipeline.last_run.id)}`
  }
  return `/datasets/${encodeURIComponent(profile.dataset.id)}`
}

function controlStatusState(status: string): string {
  if (status === 'fail') return 'error'
  if (status === 'warning' || status === 'unknown') return 'warning'
  if (status === 'pass') return 'ok'
  return 'unknown'
}

function formatDateTime(value?: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date)} UTC`
}

function SummaryMetric({
  detail,
  icon,
  label,
  value,
}: {
  detail: string
  icon: ReactNode
  label: string
  value: string | number
}) {
  return (
    <div className="phlo-observatory-dataset-summary-item">
      {icon}
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  )
}

function Fact({
  icon,
  label,
  value,
}: {
  icon?: ReactNode
  label: string
  value: string
}) {
  return (
    <>
      <dt>
        {icon}
        {label}
      </dt>
      <dd>{value}</dd>
    </>
  )
}

function EmptyRow({ label }: { label: string }) {
  return (
    <div className="phlo-observatory-mini-row">
      <span>{label}</span>
      <small>empty</small>
    </div>
  )
}
