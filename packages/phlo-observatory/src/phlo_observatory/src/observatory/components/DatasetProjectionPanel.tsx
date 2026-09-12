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
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { HealthDot } from '@/components/observatory/status'

export function DatasetProjectionPanel({
  projection,
}: {
  projection: CanonicalDatasetProjection
}) {
  const { readiness } = projection
  return (
    <div
      className="flex flex-col gap-3"
      data-candidate={projection.candidate}
      data-state={readiness.ready ? 'ok' : 'error'}
    >
      <div className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
        Canonical projection · {readiness.policy_version}
      </div>
      <FactGrid>
        <Fact label="Dataset" value={projection.dataset_id} />
        <Fact label="Table" value={projection.table_id} />
        <Fact label="Owner" value={projection.owner ?? 'unassigned'} />
        <Fact
          label="Classification"
          value={projection.classifications.join(', ') || 'none'}
        />
        <Fact
          label="Workflow state"
          value={projection.workflow_state ?? 'unknown'}
        />
        <Fact
          label="Publication"
          value={projection.publication_state ?? 'unknown'}
        />
        <Fact label="Approval" value={projection.approval_state ?? 'unknown'} />
        <Fact label="Last action" value={projection.last_action_id ?? 'none'} />
        <Fact
          label="Allowed transitions"
          value={projection.allowed_transitions.join(', ') || 'none'}
        />
      </FactGrid>
      <div className="divide-border divide-y border-y">
        {projection.controls.map((control) => (
          <ControlRow control={control} key={control.control} />
        ))}
        {projection.controls.length === 0 && (
          <MiniRow
            detail="projection carried no control set"
            state="unknown"
            title="No controls evaluated"
          />
        )}
      </div>
      <div>
        <div className="text-muted-foreground mb-1 text-[10px] font-medium tracking-widest uppercase">
          Readiness reasons (canonical order)
        </div>
        <div className="divide-border divide-y border-y">
          {readiness.reasons.length === 0 ? (
            <MiniRow
              detail="policy verdict is clear"
              state="ok"
              title="No readiness reasons"
            />
          ) : (
            readiness.reasons.map((reason) => (
              <div className="py-1.5" key={reason}>
                <span className="text-foreground font-mono text-[11px]">
                  {reason}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
      {projection.evidence.length > 0 && (
        <div>
          <div className="text-muted-foreground mb-1 text-[10px] font-medium tracking-widest uppercase">
            Evidence behind the controls
          </div>
          <div className="divide-border divide-y border-y">
            {projection.evidence.map((entry) => (
              <MiniRow
                detail={entry.source}
                key={`${entry.kind}:${entry.subject}`}
                state={entry.status}
                title={`${entry.kind} · ${entry.subject}`}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function MiniRow({
  detail,
  state,
  title,
}: {
  detail: string
  state?: string
  title: string
}) {
  return (
    <div className="flex items-start gap-2 py-1.5">
      <HealthDot className="mt-1" state={state} />
      <span className="min-w-0">
        <span className="text-foreground block font-mono text-[11px]">
          {title}
        </span>
        <span className="text-muted-foreground block font-mono text-[10px]">
          {detail}
        </span>
      </span>
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
    <MiniRow
      detail={control.severity ?? 'control'}
      state={state}
      title={`${control.control} · ${control.status}`}
    />
  )
}
