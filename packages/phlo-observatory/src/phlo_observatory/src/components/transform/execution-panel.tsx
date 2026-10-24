/** Candidate execution preserves immutable source and real native run identity. */
import * as React from 'react'
import type {
  TransformCompilation,
  TransformDraft,
  TransformExecution,
} from '@/lib/data/api/transform-editor'
import {
  archiveTransformExecution,
  executeTransformDraft,
  getTransformExecutions,
  getTransformHandoff,
  reclaimTransformExecution,
  reconcileTransformExecution,
} from '@/lib/data/api/transform-editor'
import { ReclaimAction } from '@/components/phlo/reclaim-action'
import { getBranchesPage } from '@/lib/data/api/branches'
import { ActionButton as Button } from '@/components/phlo/action-button'
import { Badge } from '@/components/ui/badge'
import { Segmented } from '@/components/ui/toggle-group'
import {
  ArchiveEvidenceAction,
  EvidenceStatus,
  SourceHandoffPanel,
} from '@/components/phlo/retained-evidence'
import { ActionGroup } from '@/components/ui/action-group'

type Props = {
  env: string
  model: string
  draft: TransformDraft
  compilation?: TransformCompilation
  dirty: boolean
}
function useCandidateExecution({ env, model, draft }: Props) {
  const [executions, setExecutions] = React.useState<Array<TransformExecution>>(
    [],
  )
  const [selectedId, setSelectedId] = React.useState<string>()
  const [error, setError] = React.useState<string>()
  const [busy, setBusy] = React.useState(false)
  const [archived, setArchived] = React.useState(false)
  const mounted = React.useRef(true)
  const intent = React.useRef<{ key: string; baseHash: string } | undefined>(
    undefined,
  )
  React.useEffect(() => {
    let active = true
    mounted.current = true
    getTransformExecutions({
      data: {
        env,
        model,
        draft_id: draft.draft_id,
        revision: draft.revision,
        include_archived: archived,
      },
    }).then(
      (page) => {
        if (active)
          setExecutions((current) =>
            mergeExecutions(current, page.items).filter(
              (item) => archived || !item.archived_at,
            ),
          )
      },
      (caught) => {
        if (active)
          setError(
            caught instanceof Error
              ? caught.message
              : 'Could not read candidate executions.',
          )
      },
    )
    return () => {
      active = false
      mounted.current = false
    }
  }, [env, model, draft.draft_id, draft.revision, archived])
  const run = async () => {
    if (busy || draft.archived_at) return
    setBusy(true)
    setError(undefined)
    try {
      if (!intent.current) {
        const page = await getBranchesPage({ data: { env } })
        const base = page.branches.find((branch) => branch.name === draft.ref)
        if (!base) throw new Error('The source data branch is unavailable.')
        intent.current = { key: crypto.randomUUID(), baseHash: base.hash }
      }
      const result = await executeTransformDraft({
        data: {
          env,
          model,
          draft_id: draft.draft_id,
          revision: draft.revision,
          revision_digest: draft.revision_digest,
          base_hash: intent.current.baseHash,
          idempotency_key: intent.current.key,
        },
      })
      if ('rejected_before_write' in result) {
        intent.current = undefined
        throw new Error(result.message)
      }
      if (mounted.current) {
        setExecutions((current) => mergeExecutions(current, [result]))
        setSelectedId(result.execution_id)
        intent.current = undefined
      }
    } catch (caught) {
      if (mounted.current)
        setError(
          caught instanceof Error
            ? caught.message
            : 'Candidate execution failed.',
        )
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const execution =
    executions.find((item) => item.execution_id === selectedId) ??
    executions.at(-1)
  const update = (value: TransformExecution) =>
    setExecutions((current) => mergeExecutions(current, [value]))
  return {
    execution,
    executions,
    choose: setSelectedId,
    error,
    busy,
    run,
    archived,
    setArchived,
    update,
  }
}
function readyToExecute(props: Props) {
  return (
    props.compilation?.status === 'ready' &&
    props.compilation.revision_digest === props.draft.revision_digest &&
    !props.draft.archived_at &&
    !props.dirty
  )
}
export function CandidateExecutionPanel(props: Props) {
  const {
    execution,
    executions,
    choose,
    error,
    busy,
    run,
    archived,
    setArchived,
    update,
  } = useCandidateExecution(props)
  const [view, setView] = React.useState('preview')
  const ready = readyToExecute(props)
  return (
    <section
      aria-label="Candidate execution"
      className="grid min-w-0 max-w-full shrink-0 gap-3 border-t border-line p-4 text-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="m-0 font-medium">Candidate execution</p>
          <p className="mb-0 text-muted-foreground">
            Build saved revision {props.draft.revision} on a separate data
            branch. The base reference is preserved.
          </p>
        </div>
        <Button
          operation="v1_transform_model_execute"
          variant="outline"
          disabled={!ready || busy}
          onClick={() => void run()}
        >
          {busy
            ? 'Running candidate…'
            : execution
              ? 'Run new candidate'
              : 'Run candidate'}
        </Button>
      </div>
      <Segmented
        aria-label="Candidate result view"
        value={view}
        onValueChange={setView}
        options={[
          { value: 'preview', label: 'Preview' },
          { value: 'history', label: `Run history · ${executions.length}` },
          { value: 'diagnostics', label: 'Diagnostics' },
        ]}
      />
      {view === 'history' ? (
        <label className="flex items-center gap-2 text-muted-foreground">
          <input
            type="checkbox"
            checked={archived}
            onChange={(event) => setArchived(event.target.checked)}
          />
          Include archived runs
        </label>
      ) : null}
      {view === 'history' ? (
        <ExecutionHistory
          executions={executions}
          selectedId={execution?.execution_id}
          onChoose={(id) => {
            choose(id)
            setView('preview')
          }}
        />
      ) : null}
      {execution && view !== 'history' ? (
        <ExecutionEvidence
          execution={execution}
          diagnostics={view === 'diagnostics'}
        />
      ) : view !== 'history' ? (
        <p className="m-0 text-muted-foreground">
          {ready
            ? 'This saved revision is ready to run as a candidate.'
            : 'Compile this saved revision before running its candidate.'}{' '}
          Candidate tables are separate from published assets.
        </p>
      ) : null}
      {execution && view !== 'history' ? (
        <ExecutionLifecycle
          key={JSON.stringify([
            execution.execution_id,
            execution.archived_at,
            execution.reclaimed_at,
            execution.reclaim_started_at,
            props.draft.archived_at,
            props.draft.reclaimed_at,
            props.draft.reclaim_started_at,
          ])}
          execution={execution}
          onChanged={update}
        />
      ) : null}
      {error ? (
        <p role="alert" className="m-0 text-bad-text">
          {error}
        </p>
      ) : null}
    </section>
  )
}
function ExecutionEvidence({
  execution,
  diagnostics,
}: {
  execution: TransformExecution
  diagnostics: boolean
}) {
  const report = execution.report
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant={execution.status === 'passed' ? 'ok' : 'warn'}>
          {execution.status}
        </Badge>
        <span>Revision {execution.revision}</span>
        <span className="break-all font-mono">
          {execution.candidate_ref ?? 'Candidate provisioning pending'}
        </span>
      </div>
      {report ? (
        <p className="m-0 break-all font-mono text-muted-foreground">
          Native run {String(report.run_id)} · plan {String(report.plan_id)}
        </p>
      ) : null}
      <EvidenceStatus evidence={execution.evidence} />
      {!diagnostics &&
      execution.preview &&
      execution.evidence?.stale !== true ? (
        <CandidateRows preview={execution.preview} />
      ) : null}
      {!diagnostics && execution.status === 'passed' && !execution.preview ? (
        <p className="m-0 text-muted-foreground">
          Recorded native execution passed. Its candidate snapshot preview is
          unavailable.
        </p>
      ) : null}
      {diagnostics && report ? (
        <details>
          <summary className="cursor-pointer text-muted-foreground">
            Native run events and model results
          </summary>
          <pre className="max-h-64 overflow-auto rounded-lg bg-background p-3 whitespace-pre-wrap">
            {JSON.stringify(report, null, 2)}
          </pre>
        </details>
      ) : null}
    </div>
  )
}
function CandidateRows({ preview }: { preview: Record<string, unknown> }) {
  const columns = Array.isArray(preview.columns)
    ? preview.columns.filter(
        (column): column is string => typeof column === 'string',
      )
    : []
  const rows = Array.isArray(preview.rows)
    ? preview.rows.filter(
        (row): row is Record<string, unknown> =>
          Boolean(row) && typeof row === 'object',
      )
    : []
  return (
    <div className="grid gap-2">
      <p className="m-0 text-muted-foreground">
        Recorded candidate output · {String(preview.row_count)} rows · snapshot{' '}
        {String(preview.snapshot_id)}
      </p>
      <div className="max-h-72 min-w-0 max-w-full overflow-auto rounded-lg border border-line">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  className="border-b border-line bg-background px-3 py-2 font-medium"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td
                    key={column}
                    className="border-b border-line px-3 py-2 font-mono whitespace-nowrap"
                  >
                    {row[column] === null ? 'NULL' : String(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {preview.has_more ? (
        <p className="m-0 text-muted-foreground">
          Showing the first {rows.length} observed rows.
        </p>
      ) : null}
    </div>
  )
}

function mergeExecutions(
  before: Array<TransformExecution>,
  after: Array<TransformExecution>,
) {
  const records = new Map(
    [...before, ...after].map((item) => [item.execution_id, item]),
  )
  return [...records.values()].sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  )
}
function ExecutionHistory({
  executions,
  selectedId,
  onChoose,
}: {
  executions: Array<TransformExecution>
  selectedId?: string
  onChoose: (id: string) => void
}) {
  if (!executions.length)
    return (
      <p className="m-0 text-muted-foreground">
        No native candidate runs are recorded for this saved revision.
      </p>
    )
  return (
    <div aria-label="Recorded candidate runs" className="grid min-w-0 gap-2">
      {[...executions].reverse().map((item) => (
        <button
          key={item.execution_id}
          type="button"
          aria-pressed={item.execution_id === selectedId}
          onClick={() => onChoose(item.execution_id)}
          className="flex min-w-0 flex-wrap items-start gap-3 rounded-md border border-line bg-background px-3 py-2 text-left hover:bg-branch-soft"
        >
          <Badge variant={item.status === 'passed' ? 'ok' : 'warn'}>
            {item.status}
            {item.archived_at ? ' · Archived' : ''}
          </Badge>
          <span className="min-w-0 flex-1">
            <span className="block break-all font-mono">
              {String(item.report?.run_id ?? item.execution_id)}
            </span>
            <span className="block break-all text-muted-foreground">
              {item.candidate_ref ?? 'Candidate provisioning pending'}
            </span>
          </span>
          <span className="text-muted-foreground">
            {new Date(item.created_at).toLocaleString()}
          </span>
        </button>
      ))}
    </div>
  )
}

function ExecutionReclamation({
  execution,
  disabled,
  onChanged,
}: {
  execution: TransformExecution
  disabled: boolean
  onChanged: (value: TransformExecution) => void
}) {
  if (!execution.archived_at || execution.reclaimed_at) return null
  return (
    <ReclaimAction
      identity={execution.execution_id}
      label={
        execution.reclaim_started_at
          ? 'Retry payload reclamation'
          : 'Reclaim execution payload'
      }
      description="Permanently remove local payloads for this archived native execution. Reports, source handoff, identity, hashes and operation claims remain. Provider tables and catalogs are unchanged; mutation fences are not released."
      operation="v1_transform_model_execution_reclaim"
      disabled={
        disabled ||
        ['prepared', 'running', 'unknown'].includes(execution.status)
      }
      onReclaim={async (reason) =>
        onChanged(
          await reclaimTransformExecution({
            data: {
              env: execution.env,
              model: execution.model,
              draft_id: execution.draft_id,
              revision: execution.revision,
              execution_id: execution.execution_id,
              confirmed: true,
              reason,
              expected_revision_digest: execution.revision_digest,
            },
          }),
        )
      }
    />
  )
}

function ExecutionLifecycle({
  execution,
  onChanged,
}: {
  execution: TransformExecution
  onChanged: (value: TransformExecution) => void
}) {
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const identity = {
    env: execution.env,
    model: execution.model,
    draft_id: execution.draft_id,
    revision: execution.revision,
    execution_id: execution.execution_id,
  }
  async function reconcile() {
    setBusy(true)
    setError('')
    try {
      onChanged(await reconcileTransformExecution({ data: identity }))
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Reconciliation is unavailable.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="grid min-w-0 gap-3">
      <ActionGroup>
        <Button
          variant="outline"
          operation="v1_transform_model_execution_reconcile"
          disabled={busy}
          onClick={() => void reconcile()}
        >
          Reconcile evidence
        </Button>
        <ArchiveEvidenceAction
          operation="v1_transform_model_execution_archive"
          archived={Boolean(execution.archived_at)}
          reason={execution.archive_reason}
          disabled={
            busy ||
            Boolean(execution.reclaimed_at || execution.reclaim_started_at)
          }
          onChange={async (input) =>
            onChanged(
              await archiveTransformExecution({
                data: { ...identity, ...input },
              }),
            )
          }
        />
        <ExecutionReclamation
          execution={execution}
          disabled={busy}
          onChanged={onChanged}
        />
      </ActionGroup>
      {execution.archived_at &&
      ['prepared', 'running', 'unknown'].includes(execution.status) ? (
        <p className="m-0 text-xs text-muted-foreground">
          Active or unresolved execution payloads cannot be reclaimed. Reconcile
          durable evidence first.
        </p>
      ) : null}
      <p className="m-0 text-xs text-muted-foreground">
        Reconciliation reads durable reports and provider snapshots. It never
        reruns a candidate or releases an unknown mutation fence.
      </p>
      {execution.reconciliation ? (
        <p role="status" className="m-0 text-xs">
          Reconciliation:{' '}
          {execution.reconciliation.resolution === 'verified_success'
            ? 'verified success'
            : 'unresolved'}
          . {execution.reconciliation.reason}
        </p>
      ) : null}
      {execution.archived_at ? (
        <p className="m-0 text-xs text-muted-foreground">
          Archived {new Date(execution.archived_at).toLocaleString()} by{' '}
          {execution.archived_by}. {execution.archive_reason}
          {execution.reclaimed_at || execution.reclaim_started_at
            ? ` Local reclamation ${execution.reclaimed_at ? 'completed' : 'started'}; restoration is unavailable. ${execution.reclaimed_bytes ?? 0} local bytes reclaimed. Reports and source handoff remain.`
            : ''}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 text-xs text-bad-text">
          {error}
        </p>
      ) : null}
      <SourceHandoffPanel
        operation="v1_transform_model_draft_handoff"
        load={() => getTransformHandoff({ data: identity })}
      />
    </div>
  )
}
