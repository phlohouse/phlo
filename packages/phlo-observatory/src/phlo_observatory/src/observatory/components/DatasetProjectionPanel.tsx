/**
 * Shared canonical Dataset projection renderer.
 *
 * One component renders the exact projection `DatasetAuthority.projection()`
 * serves: identity, publication and workflow state, controls, evidence, and
 * the readiness verdict with its ordered reasons. Catalog, Governance,
 * Publishing, and the Dataset Profile all mount this panel so no surface
 * keeps its own policy rendering or a second canonical store.
 */
import type {
  CanonicalDatasetControlEntry,
  CanonicalDatasetProjection,
} from '@/observatory/api/types'

export function DatasetProjectionPanel({
  projection,
}: {
  projection: CanonicalDatasetProjection
}) {
  const { readiness } = projection
  return (
    <div
      className="phlo-observatory-dataset-projection"
      data-candidate={projection.candidate}
      data-state={readiness.ready ? 'ok' : 'error'}
    >
      <div className="phlo-observatory-inspector-label">
        Canonical projection · {readiness.policy_version}
      </div>
      <dl className="phlo-observatory-facts">
        <dt>Dataset</dt>
        <dd>{projection.dataset_id}</dd>
        <dt>Table</dt>
        <dd>{projection.table_id}</dd>
        <dt>Owner</dt>
        <dd>{projection.owner ?? 'unassigned'}</dd>
        <dt>Classification</dt>
        <dd>{projection.classifications.join(', ') || 'none'}</dd>
        <dt>Workflow state</dt>
        <dd>{projection.workflow_state ?? 'unknown'}</dd>
        <dt>Publication</dt>
        <dd>{projection.publication_state ?? 'unknown'}</dd>
        <dt>Approval</dt>
        <dd>{projection.approval_state ?? 'unknown'}</dd>
        <dt>Last action</dt>
        <dd>{projection.last_action_id ?? 'none'}</dd>
        <dt>Allowed transitions</dt>
        <dd>{projection.allowed_transitions.join(', ') || 'none'}</dd>
      </dl>
      <div className="phlo-observatory-detail-list">
        {projection.controls.map((control) => (
          <ControlRow control={control} key={control.control} />
        ))}
        {projection.controls.length === 0 && (
          <div className="phlo-observatory-mini-row" data-state="unknown">
            <span>No controls evaluated</span>
            <small>projection carried no control set</small>
          </div>
        )}
      </div>
      <div className="phlo-observatory-inspector-section-label">
        Readiness reasons (canonical order)
      </div>
      <div className="phlo-observatory-detail-list">
        {readiness.reasons.length === 0 ? (
          <div className="phlo-observatory-mini-row" data-state="ok">
            <span>No readiness reasons</span>
            <small>policy verdict is clear</small>
          </div>
        ) : (
          readiness.reasons.map((reason) => (
            <div className="phlo-observatory-mini-row" key={reason}>
              <span>{reason}</span>
            </div>
          ))
        )}
      </div>
      {projection.evidence.length > 0 && (
        <>
          <div className="phlo-observatory-inspector-section-label">
            Evidence behind the controls
          </div>
          <div className="phlo-observatory-detail-list">
            {projection.evidence.map((entry) => (
              <div
                className="phlo-observatory-mini-row"
                data-state={entry.status}
                key={`${entry.kind}:${entry.subject}`}
              >
                <span>
                  {entry.kind} · {entry.subject}
                </span>
                <small>{entry.source}</small>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function ControlRow({ control }: { control: CanonicalDatasetControlEntry }) {
  const state =
    control.status === 'passed'
      ? 'ok'
      : control.status === 'failed'
        ? 'error'
        : 'unknown'
  return (
    <div className="phlo-observatory-mini-row" data-state={state}>
      <span>
        {control.control} · {control.status}
      </span>
      <small>{control.severity ?? 'control'}</small>
    </div>
  )
}
