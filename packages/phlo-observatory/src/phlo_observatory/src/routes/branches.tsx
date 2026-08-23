/**
 * /branches route. Branch list and detail panels with branch actions routed
 * through a reducer; completed actions invalidate cached resources so new
 * refs show up immediately.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  AlertTriangle,
  Database,
  GitBranch,
  GitCompare,
  History,
  Plus,
  Table2,
} from 'lucide-react'
import { useCallback, useEffect, useReducer, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryBranch,
  ObservatoryBranchDetail,
  ObservatoryOperation,
  ObservatoryQualityCheck,
  ObservatoryResourceResult,
  ObservatoryTable,
} from '@/observatory/api/types'
import {
  getObservatoryBranchDetailDirect,
  getObservatoryBranchRecords,
  getObservatoryOperationRecords,
  getObservatoryQualityRecords,
  runObservatoryBranchAction,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  invalidateCachedResources,
  useLiveResource,
} from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/branches')({
  component: Branches,
})

type BranchesState = {
  actionMessage: string | null
  activePanel: BranchPanel
  createdBranches: Array<ObservatoryBranch>
  detail: ObservatoryResourceResult<ObservatoryBranchDetail>
  selectedId: string | null
}

type BranchesAction =
  | { type: 'actionMessage'; message: string | null }
  | { type: 'activePanel'; panel: BranchPanel }
  | {
      type: 'detail'
      detail: ObservatoryResourceResult<ObservatoryBranchDetail>
    }
  | { type: 'select'; selectedId: string | null }
  | { type: 'branchCreated'; branch: ObservatoryBranch; message: string | null }

function branchesReducer(
  state: BranchesState,
  action: BranchesAction,
): BranchesState {
  switch (action.type) {
    case 'actionMessage':
      return { ...state, actionMessage: action.message }
    case 'activePanel':
      return { ...state, activePanel: action.panel }
    case 'detail':
      return { ...state, detail: action.detail }
    case 'select':
      return { ...state, selectedId: action.selectedId }
    case 'branchCreated':
      return {
        ...state,
        actionMessage: action.message,
        createdBranches: mergeBranches(state.createdBranches, [action.branch]),
        selectedId: action.branch.id,
      }
  }
}

export function Branches() {
  const result = useLiveResource(
    getObservatoryBranchRecords,
    60_000,
    'observatory:branches',
  )
  const operationsResult = useLiveResource(
    getObservatoryOperationRecords,
    60_000,
    'observatory:operations',
  )
  const qualityResult = useLiveResource(
    getObservatoryQualityRecords,
    60_000,
    'observatory:quality',
  )
  const [
    { actionMessage, activePanel, createdBranches, detail, selectedId },
    dispatch,
  ] = useReducer(branchesReducer, {
    actionMessage: null,
    activePanel: 'compare',
    createdBranches: [],
    detail: {
      data: null,
      error: null,
    },
    selectedId: null,
  })
  const [branchDraftOpen, setBranchDraftOpen] = useState(false)
  const [branchDraftName, setBranchDraftName] = useState('')
  const [isCreatingBranch, setIsCreatingBranch] = useState(false)
  const isLoading =
    result.isLoading || operationsResult.isLoading || qualityResult.isLoading
  const branches = mergeBranches(createdBranches, result.data ?? [])
  const selected =
    branches.find((branch) => branch.id === selectedId) ??
    branches.find((branch) => branch.current) ??
    branches[0]
  const selectedCompare = detail.data?.compare ?? branchCompare(selected)
  const selectedTableCount =
    detail.data?.tables.length ?? metadataNumber(selected, 'tables')
  const branchOperations = branchRelatedOperations(
    selected,
    operationsResult.data ?? [],
  )
  const selectedEvidenceCount = mergeOperations(
    branchOperations,
    detail.data?.commits ?? [],
  ).length
  const selectedTables = detail.data?.tables ?? []
  const selectedQuality = qualityForTables(
    selectedTables,
    qualityResult.data ?? [],
  )
  const activeBlockingQuality = blockingQualityChecks(selectedQuality).length
  const providerState = isLoading
    ? 'Reading branch state from the live lakehouse.'
    : branches.length === 1 && selected?.current
      ? 'Only the protected baseline is available.'
      : `${branches.length} ${pluralize(branches.length, 'branch', 'branches')} available.`
  const createBranch = useCallback(() => {
    const branchName = branchDraftName.trim()
    if (!branchName || isCreatingBranch) return
    setIsCreatingBranch(true)
    void runObservatoryBranchAction({
      data: { actionId: `branch:create:${branchName}` },
    })
      .then((next) => {
        invalidateCachedResources([
          'observatory:operations',
          'observatory:branches',
        ])
        const message =
          next.data?.message ?? next.error ?? 'Branch action completed'
        if (next.data?.status === 'succeeded') {
          const url = new URL(window.location.href)
          url.searchParams.set('branchId', branchName)
          window.history.replaceState(
            null,
            '',
            `${url.pathname}?${url.searchParams.toString()}`,
          )
          dispatch({
            type: 'branchCreated',
            branch: {
              current: false,
              id: branchName,
              metadata: { source: 'local' },
              name: branchName,
              protected: false,
            },
            message,
          })
          setBranchDraftName('')
          setBranchDraftOpen(false)
        } else {
          dispatch({ type: 'actionMessage', message })
        }
      })
      .finally(() => setIsCreatingBranch(false))
  }, [branchDraftName, isCreatingBranch])
  const selectBranch = useCallback((branchId: string) => {
    dispatch({ type: 'select', selectedId: branchId })
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('branchId', branchId)
    window.history.replaceState(
      null,
      '',
      `${url.pathname}?${url.searchParams.toString()}`,
    )
  }, [])

  useEffect(() => {
    const branchId = selected?.id
    if (!branchId) {
      dispatch({ type: 'detail', detail: { data: null, error: null } })
      return
    }
    let cancelled = false
    dispatch({ type: 'detail', detail: { data: null, error: null } })
    void getObservatoryBranchDetailDirect({ branchName: branchId }).then(
      (next) => {
        if (!cancelled) dispatch({ type: 'detail', detail: next })
      },
    )
    return () => {
      cancelled = true
    }
  }, [selected?.id])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'branchId',
    )
    if (!requested || requested === selectedId) return
    if (branches.some((branch) => branch.id === requested)) {
      dispatch({ type: 'select', selectedId: requested })
    }
  }, [branches, selectedId])

  return (
    <ObservatoryPage
      kicker="Review"
      title="Change review"
      description="Review branch state, table drift, quality impact, and guarded change workflows."
      action={
        <span className="phlo-observatory-pill">
          {isLoading
            ? 'Loading'
            : `${branches.length} ${pluralize(branches.length, 'branch', 'branches')}`}
        </span>
      }
    >
      <section className="phlo-observatory-surface-grid phlo-observatory-branch-grid">
        <div className="phlo-observatory-branch-main">
          <div className="phlo-observatory-list-surface">
            <div className="phlo-observatory-browser-toolbar">
              <span>
                <GitBranch className="size-4" />
                Change reviews
              </span>
              <button
                aria-expanded={branchDraftOpen}
                onClick={() => setBranchDraftOpen((open) => !open)}
                type="button"
              >
                <Plus className="size-3.5" />
                Branch
              </button>
            </div>
            {branchDraftOpen && (
              <form
                className="phlo-observatory-branch-create-panel"
                onSubmit={(event) => {
                  event.preventDefault()
                  createBranch()
                }}
              >
                <label>
                  <span>Branch name</span>
                  <input
                    autoFocus
                    onChange={(event) => setBranchDraftName(event.target.value)}
                    placeholder="review/revenue-fix"
                    value={branchDraftName}
                  />
                </label>
                <p>
                  Creates review branch state through phlo-api, then opens the
                  new branch evidence.
                </p>
                <div className="phlo-observatory-inline-actions">
                  <button
                    disabled={!branchDraftName.trim() || isCreatingBranch}
                    type="submit"
                  >
                    {isCreatingBranch ? 'Creating' : 'Create branch'}
                  </button>
                  <button
                    onClick={() => {
                      setBranchDraftName('')
                      setBranchDraftOpen(false)
                    }}
                    type="button"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
            {branches.map((branch) => (
              <button
                className="phlo-observatory-row phlo-observatory-select-row"
                data-active={branch.id === selected?.id}
                key={branch.id}
                onClick={() => selectBranch(branch.id)}
                type="button"
              >
                <div className="phlo-observatory-row-main">
                  <div className="phlo-observatory-row-title">
                    {branch.name}
                  </div>
                  <div className="phlo-observatory-row-meta">
                    {branch.current ? 'Current branch' : 'Review branch'}
                    {branchDelta(branch) && <> · {branchDelta(branch)}</>}
                  </div>
                </div>
                <span className="phlo-observatory-pill">
                  {branch.current
                    ? 'current'
                    : branch.protected
                      ? 'protected'
                      : 'branch'}
                </span>
              </button>
            ))}
          </div>
          {selected && (
            <>
              <section className="phlo-observatory-branch-summary">
                <div className="phlo-observatory-branch-summary-copy">
                  <div className="phlo-observatory-inspector-label">
                    Selected branch
                  </div>
                  <h2>{selected.name}</h2>
                  <p>
                    {detail.data
                      ? branchNarrative(detail.data)
                      : branchNarrativeFromBranch(selected)}
                  </p>
                  <p>{providerState}</p>
                </div>
                <div className="phlo-observatory-action-row">
                  <button
                    data-active={activePanel === 'compare'}
                    onClick={() =>
                      dispatch({ type: 'activePanel', panel: 'compare' })
                    }
                    type="button"
                  >
                    <GitCompare className="size-3.5" />
                    Compare
                  </button>
                  <button
                    data-active={activePanel === 'history'}
                    onClick={() =>
                      dispatch({ type: 'activePanel', panel: 'history' })
                    }
                    type="button"
                  >
                    <History className="size-3.5" />
                    History
                  </button>
                  <button
                    data-active={activePanel === 'contents'}
                    onClick={() =>
                      dispatch({ type: 'activePanel', panel: 'contents' })
                    }
                    type="button"
                  >
                    <Table2 className="size-3.5" />
                    Contents
                  </button>
                </div>
                <dl className="phlo-observatory-branch-facts">
                  <div>
                    <dt>Tables</dt>
                    <dd>{selectedTableCount}</dd>
                  </div>
                  <div>
                    <dt>Evidence</dt>
                    <dd>{selectedEvidenceCount}</dd>
                  </div>
                  <div>
                    <dt>Blocking quality</dt>
                    <dd>{activeBlockingQuality}</dd>
                  </div>
                  <div>
                    <dt>Added</dt>
                    <dd>{selectedCompare.added ?? 0}</dd>
                  </div>
                  <div>
                    <dt>Changed</dt>
                    <dd>{selectedCompare.changed ?? 0}</dd>
                  </div>
                  <div>
                    <dt>Ahead / behind</dt>
                    <dd>
                      {selectedCompare.ahead ?? 0} /{' '}
                      {selectedCompare.behind ?? 0}
                    </dd>
                  </div>
                </dl>
              </section>
              <BranchReadiness
                branch={selected}
                operations={mergeOperations(
                  branchOperations,
                  detail.data?.commits ?? [],
                )}
                quality={selectedQuality}
                tables={selectedTables}
              />
              {detail.data ? (
                <BranchPanelView
                  active={activePanel}
                  detail={detail.data}
                  operations={mergeOperations(
                    branchOperations,
                    detail.data.commits,
                  )}
                  quality={selectedQuality}
                />
              ) : (
                <BranchPanelFallback
                  active={activePanel}
                  branch={selected}
                  operations={branchOperations}
                />
              )}
            </>
          )}
          {detail.error && (
            <div className="phlo-observatory-panel-footer">{detail.error}</div>
          )}
          {actionMessage && (
            <div className="phlo-observatory-panel-footer">{actionMessage}</div>
          )}
          {result.error && (
            <div className="phlo-observatory-panel-footer">{result.error}</div>
          )}
          {operationsResult.error && (
            <div className="phlo-observatory-panel-footer">
              {operationsResult.error}
            </div>
          )}
          {qualityResult.error && (
            <div className="phlo-observatory-panel-footer">
              {qualityResult.error}
            </div>
          )}
        </div>
        <BranchInspector
          branch={selected}
          detail={detail.data}
          isLoading={isLoading}
          operations={mergeOperations(
            branchOperations,
            detail.data?.commits ?? [],
          )}
          providerState={providerState}
          quality={selectedQuality}
          tables={selectedTables}
        />
      </section>
    </ObservatoryPage>
  )
}

type BranchPanel = 'contents' | 'compare' | 'history'

function mergeBranches(
  left: Array<ObservatoryBranch>,
  right: Array<ObservatoryBranch>,
): Array<ObservatoryBranch> {
  const merged = new Map<string, ObservatoryBranch>()
  for (const branch of [...left, ...right]) {
    merged.set(branch.id, branch)
  }
  return Array.from(merged.values())
}

function mergeOperations(
  left: Array<ObservatoryOperation>,
  right: Array<ObservatoryOperation>,
): Array<ObservatoryOperation> {
  const merged = new Map<string, ObservatoryOperation>()
  for (const operation of [...left, ...right]) {
    merged.set(operation.id, operation)
  }
  return Array.from(merged.values())
}

function branchRelatedOperations(
  branch: ObservatoryBranch | undefined,
  operations: Array<ObservatoryOperation>,
): Array<ObservatoryOperation> {
  if (!branch) return []
  return operations.filter((operation) => {
    const metadataBranch = metadataString(operation.metadata, 'branch')
    return (
      operation.target?.kind === 'branch' &&
      (operation.target.id === branch.id ||
        operation.target.id === branch.name ||
        metadataBranch === branch.id ||
        metadataBranch === branch.name)
    )
  })
}

function qualityForTables(
  tables: Array<ObservatoryTable>,
  quality: Array<ObservatoryQualityCheck>,
): Array<ObservatoryQualityCheck> {
  const assetIds = new Set(
    tables
      .flatMap((table) => [table.asset_id, table.id])
      .filter((id): id is string => Boolean(id)),
  )
  return quality.filter((check) => assetIds.has(check.asset_id))
}

function BranchReadiness({
  branch,
  operations,
  quality,
  tables,
}: {
  branch: ObservatoryBranch
  operations: Array<ObservatoryOperation>
  quality: Array<ObservatoryQualityCheck>
  tables: Array<ObservatoryTable>
}) {
  const failing = blockingQualityChecks(quality)
  const warnings = quality.filter((check) => check.status === 'warning')
  const failedOperation = operations.find(
    (operation) => operation.status === 'failed',
  )
  const runningOperation = operations.find(
    (operation) => operation.status === 'running',
  )
  const firstTable = tables[0]
  const primaryQuality = failing[0] ?? warnings[0] ?? quality[0]
  const lineageAssetId =
    primaryQuality?.asset_id ??
    tables.find((table) => table.asset_id)?.asset_id ??
    firstTable?.id
  const state =
    failing.length > 0
      ? 'error'
      : warnings.length > 0
        ? 'warning'
        : branch.current
          ? 'ok'
          : 'unknown'
  const next =
    failing.length > 0
      ? 'Resolve blocking checks before approving change.'
      : failedOperation
        ? 'Recover failed operation evidence before approving change.'
        : runningOperation
          ? 'Wait for the running operation to complete before approving change.'
          : branch.current
            ? 'Create a review branch to evaluate proposed lakehouse changes.'
            : 'Review changed tables, impact, and approvals before publishing.'

  return (
    <section className="phlo-observatory-branch-readiness" data-state={state}>
      <div>
        <div className="phlo-observatory-inspector-label">Review state</div>
        <h3>{branch.current ? 'Protected baseline' : 'Review candidate'}</h3>
        <p>{next}</p>
      </div>
      <div className="phlo-observatory-branch-readiness-links">
        <Link
          search={primaryQuality ? { checkId: primaryQuality.id } : undefined}
          to="/quality"
        >
          Quality
        </Link>
        <Link
          search={lineageAssetId ? { assetId: lineageAssetId } : undefined}
          to="/lineage"
        >
          Lineage
        </Link>
        <Link
          search={
            (failedOperation ?? runningOperation)
              ? { operationId: (failedOperation ?? runningOperation)?.id }
              : undefined
          }
          to="/operations"
        >
          Operations
        </Link>
      </div>
      <dl>
        <div>
          <dt>Tables in scope</dt>
          <dd>{tables.length}</dd>
        </div>
        <div>
          <dt>Quality checks</dt>
          <dd>{quality.length}</dd>
        </div>
        <div>
          <dt>Failing</dt>
          <dd>{failing.length}</dd>
        </div>
        <div>
          <dt>Operation state</dt>
          <dd>
            {failedOperation
              ? 'failed'
              : runningOperation
                ? 'running'
                : 'clear'}
          </dd>
        </div>
      </dl>
    </section>
  )
}

function BranchPanelView({
  active,
  detail,
  operations,
  quality,
}: {
  active: BranchPanel
  detail: ObservatoryBranchDetail
  operations: Array<ObservatoryOperation>
  quality: Array<ObservatoryQualityCheck>
}) {
  if (active === 'compare') {
    return (
      <div className="phlo-observatory-branch-review">
        <div className="phlo-observatory-command-strip">
          <BranchMetric
            icon={<Plus className="size-4" />}
            label="Added"
            value={detail.compare.added ?? 0}
          />
          <BranchMetric
            icon={<GitCompare className="size-4" />}
            label="Changed"
            value={detail.compare.changed ?? 0}
          />
          <BranchMetric
            icon={<AlertTriangle className="size-4" />}
            label="Removed"
            value={detail.compare.removed ?? 0}
          />
        </div>
        <BranchReviewEvidence
          branch={detail.branch}
          operations={operations}
          quality={quality}
          tables={detail.tables}
        />
      </div>
    )
  }

  if (active === 'history') {
    const commits = mergeOperations(operations, detail.commits)
    return (
      <div className="phlo-observatory-detail-list">
        {commits.length > 0 ? (
          commits
            .slice(0, 8)
            .map((commit) => <CommitRow commit={commit} key={commit.id} />)
        ) : (
          <p>No operation evidence is linked to this branch yet.</p>
        )}
      </div>
    )
  }

  return (
    <div className="phlo-observatory-detail-list">
      {detail.tables.slice(0, 8).map((table) => (
        <TableRow key={table.id} table={table} />
      ))}
      {detail.tables.length === 0 && <p>No branch contents yet.</p>}
    </div>
  )
}

function BranchPanelFallback({
  active,
  branch,
  operations,
}: {
  active: BranchPanel
  branch: ObservatoryBranch
  operations: Array<ObservatoryOperation>
}) {
  if (active === 'compare') {
    const compare = branchCompare(branch)
    return (
      <div className="phlo-observatory-branch-review">
        <div className="phlo-observatory-command-strip">
          <BranchMetric
            icon={<Plus className="size-4" />}
            label="Added"
            value={compare.added ?? 0}
          />
          <BranchMetric
            icon={<GitCompare className="size-4" />}
            label="Changed"
            value={compare.changed ?? 0}
          />
          <BranchMetric
            icon={<AlertTriangle className="size-4" />}
            label="Removed"
            value={compare.removed ?? 0}
          />
        </div>
        <BranchReviewEvidence
          branch={branch}
          operations={operations}
          quality={[]}
          tables={[]}
        />
      </div>
    )
  }

  if (active === 'history' && operations.length > 0) {
    return (
      <div className="phlo-observatory-detail-list">
        {operations.slice(0, 8).map((operation) => (
          <CommitRow commit={operation} key={operation.id} />
        ))}
      </div>
    )
  }

  return (
    <div className="phlo-observatory-detail-list">
      <p>
        No branch contents are available yet. Branch operation evidence is shown
        above.
      </p>
    </div>
  )
}

function BranchMetric({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: string | number
}) {
  return (
    <div className="phlo-observatory-command-metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function BranchReviewEvidence({
  branch,
  operations,
  quality,
  tables,
}: {
  branch: ObservatoryBranch
  operations: Array<ObservatoryOperation>
  quality: Array<ObservatoryQualityCheck>
  tables: Array<ObservatoryTable>
}) {
  const report = operations.find((operation) => operation.kind === 'wap')
  const relatedOperations = report
    ? [report]
    : operations.filter((operation) =>
        ['failed', 'running', 'succeeded'].includes(operation.status),
      )
  return (
    <div className="phlo-observatory-branch-evidence">
      <div className="phlo-observatory-branch-table-list">
        <div className="phlo-observatory-inspector-label">Tables in scope</div>
        {tables.length > 0 ? (
          tables
            .slice(0, 8)
            .map((table) => <TableRow key={table.id} table={table} />)
        ) : (
          <p>
            Branch evidence reported a changed table count, but the report did
            not include table evidence.
          </p>
        )}
      </div>
      <div className="phlo-observatory-branch-table-list">
        {quality.length > 0 && (
          <div className="phlo-observatory-branch-impact-list">
            <strong>Quality impact</strong>
            {quality.slice(0, 4).map((check) => (
              <Link
                className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
                key={check.id}
                to="/quality"
                search={{ checkId: check.id }}
              >
                <span>{check.name}</span>
                <small>
                  {[qualityDatasetLabel(check), check.status, check.severity]
                    .filter(Boolean)
                    .join(' · ')}
                </small>
              </Link>
            ))}
          </div>
        )}
        {quality.length === 0 && (
          <p>No quality checks are attached to the current branch contents.</p>
        )}
        <div className="phlo-observatory-branch-impact-list">
          <strong>Operation evidence</strong>
          {relatedOperations.length > 0 ? (
            relatedOperations
              .slice(0, 3)
              .map((operation) => (
                <CommitRow commit={operation} key={operation.id} />
              ))
          ) : (
            <p>No branch operation evidence is linked to {branch.name}.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function BranchInspector({
  branch,
  detail,
  isLoading,
  operations,
  providerState,
  quality,
  tables,
}: {
  branch: ObservatoryBranch | undefined
  detail: ObservatoryBranchDetail | null
  isLoading: boolean
  operations: Array<ObservatoryOperation>
  providerState: string
  quality: Array<ObservatoryQualityCheck>
  tables: Array<ObservatoryTable>
}) {
  if (!branch) {
    return (
      <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
        <div className="phlo-observatory-inspector-label">Review evidence</div>
        <h2>{isLoading ? 'Loading branch evidence' : 'No branch selected'}</h2>
        <p>
          {isLoading
            ? 'Reading branch state from the live lakehouse.'
            : 'Branch state appears once the live lakehouse API returns a branch.'}
        </p>
      </aside>
    )
  }
  const compare = detail?.compare ?? branchCompare(branch)
  const report = operations.find((operation) => operation.kind === 'wap')
  const failing = blockingQualityChecks(quality)
  const failed = operations.filter((operation) => operation.status === 'failed')
  const running = operations.filter(
    (operation) => operation.status === 'running',
  )
  const approvalState = branchApprovalState(branch, failing, failed, running)
  return (
    <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
      <div className="phlo-observatory-inspector-label">Review evidence</div>
      <h2>{branch.name}</h2>
      <p>
        {branch.current || branch.protected
          ? 'Protected baseline branch.'
          : 'Review branch awaiting approval.'}
      </p>
      <dl className="phlo-observatory-facts">
        <dt>State</dt>
        <dd>
          {branch.protected
            ? 'protected'
            : branch.current
              ? 'current'
              : 'review'}
        </dd>
        <dt>Tables</dt>
        <dd>{tables.length || metadataNumber(branch, 'tables')}</dd>
        <dt>Changed</dt>
        <dd>{compare.changed ?? 0}</dd>
        <dt>Blocking quality</dt>
        <dd>{failing.length}</dd>
        <dt>Approval</dt>
        <dd>{approvalState}</dd>
      </dl>
      <div className="phlo-observatory-detail-list">
        <div className="phlo-observatory-mini-row">
          <span>Branch runtime</span>
          <small>{providerState}</small>
        </div>
        {report ? (
          <CommitRow commit={report} />
        ) : operations.length > 0 ? (
          <div className="phlo-observatory-mini-row">
            <span>Branch operation evidence</span>
            <small>
              {[
                failed.length > 0 ? `${failed.length} failed` : null,
                running.length > 0 ? `${running.length} running` : null,
                operations.length > 0 ? `${operations.length} total` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </small>
          </div>
        ) : (
          <div className="phlo-observatory-mini-row">
            <span>Branch operation</span>
            <small>No operation evidence linked</small>
          </div>
        )}
        {failed.slice(0, 2).map((operation) => (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            key={operation.id}
            search={{ operationId: operation.id }}
            to="/operations"
          >
            <span>{operation.name}</span>
            <small>{operation.health.message ?? operation.status}</small>
          </Link>
        ))}
        {failing.slice(0, 3).map((check) => (
          <Link
            className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
            key={check.id}
            search={{ checkId: check.id }}
            to="/quality"
          >
            <span>{check.name}</span>
            <small>
              {[qualityDatasetLabel(check), check.severity]
                .filter(Boolean)
                .join(' · ')}
            </small>
          </Link>
        ))}
      </div>
    </aside>
  )
}

function TableRow({ table }: { table: ObservatoryTable }) {
  return (
    <Link
      className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
      search={{ tableId: table.id }}
      to="/tables"
    >
      <span>
        <Database className="size-3.5" />
        {table.name}
      </span>
      <small>
        {[table.namespace, table.format, `${tableRecordCount(table)} records`]
          .filter(Boolean)
          .join(' · ')}
      </small>
    </Link>
  )
}

function CommitRow({ commit }: { commit: ObservatoryOperation }) {
  const reason = operationReason(commit)
  const sourceHash = metadataString(commit.metadata, 'source_hash')
  const targetHash = metadataString(commit.metadata, 'target_hash_after')
  const hashMovement =
    sourceHash && targetHash ? `${sourceHash} -> ${targetHash}` : null
  return (
    <Link
      className={`phlo-observatory-mini-row${
        commit.kind === 'wap' ? ' phlo-observatory-wap-history-row' : ''
      } phlo-observatory-linked-mini-row`}
      to="/operations"
      search={{ operationId: commit.id }}
    >
      <span>{commit.name}</span>
      <small>
        {[
          commit.status,
          formatDateTime(commit.completed_at),
          hashMovement,
          reason,
        ]
          .filter(Boolean)
          .join(' · ')}
      </small>
    </Link>
  )
}

function branchNarrative(detail: ObservatoryBranchDetail): string {
  const changed = detail.compare.changed ?? 0
  const added = detail.compare.added ?? 0
  const removed = detail.compare.removed ?? 0
  const direction =
    detail.branch.current || detail.branch.protected
      ? 'Protected baseline'
      : 'Review candidate'
  return `${direction}: ${detail.tables.length} tables, ${changed} changed, ${added} added, ${removed} removed.`
}

function branchNarrativeFromBranch(branch: ObservatoryBranch): string {
  const compare = branchCompare(branch)
  const direction =
    branch.current || branch.protected
      ? 'Protected baseline'
      : 'Review candidate'
  return `${direction}: ${metadataNumber(branch, 'tables')} tables, ${compare.changed ?? 0} changed, ${compare.added ?? 0} added, ${compare.removed ?? 0} removed.`
}

function branchDelta(branch: ObservatoryBranch): string | null {
  const ahead = metadataNumber(branch, 'ahead')
  const behind = metadataNumber(branch, 'behind')
  const changed = metadataNumber(branch, 'changed')
  if (ahead || behind) return `${ahead} ahead / ${behind} behind`
  if (changed) return `${changed} changed`
  return null
}

function branchApprovalState(
  branch: ObservatoryBranch,
  failing: Array<ObservatoryQualityCheck>,
  failed: Array<ObservatoryOperation>,
  running: Array<ObservatoryOperation>,
): string {
  const explicit = branch.metadata.approval_state
  if (typeof explicit === 'string' && explicit.trim()) return explicit
  if (branch.current || branch.protected) return 'baseline'
  if (failing.length > 0 || failed.length > 0) return 'blocked'
  if (running.length > 0) return 'waiting'
  return 'ready for review'
}

function blockingQualityChecks(
  quality: Array<ObservatoryQualityCheck>,
): Array<ObservatoryQualityCheck> {
  return quality.filter((check) => check.blocking && check.status !== 'passing')
}

function qualityDatasetLabel(check: ObservatoryQualityCheck): string {
  return (
    metadataString(check.metadata, 'dataset') ??
    metadataString(check.metadata, 'dataset_name') ??
    metadataString(check.metadata, 'dataset_id') ??
    `Dataset ${check.asset_id}`
  )
}

function branchCompare(branch?: ObservatoryBranch): Record<string, number> {
  if (!branch) return {}
  return {
    added: metadataNumber(branch, 'added'),
    changed: metadataNumber(branch, 'changed'),
    removed: metadataNumber(branch, 'removed'),
    ahead: metadataNumber(branch, 'ahead'),
    behind: metadataNumber(branch, 'behind'),
  }
}

function tableRecordCount(table: ObservatoryTable): string {
  const records = table.metadata.records
  if (typeof records === 'number' || typeof records === 'string') {
    return String(records)
  }
  return 'not reported'
}

function metadataNumber(
  item: ObservatoryBranch | undefined,
  key: string,
): number {
  if (!item) return 0
  const value = item.metadata[key]
  if (typeof value === 'number') return value
  if (typeof value === 'string') {
    const parsed = Number.parseInt(value, 10)
    return Number.isNaN(parsed) ? 0 : parsed
  }
  return 0
}

function metadataString(
  metadata: Record<string, unknown>,
  key: string,
): string | null {
  const value = metadata[key]
  if (typeof value === 'string' && value.length > 0) return value
  if (typeof value === 'number') return String(value)
  return null
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

function operationReason(operation: ObservatoryOperation): string | null {
  const reason = operation.metadata.failure_reason ?? operation.health.message
  return typeof reason === 'string' && reason ? reason : null
}

function pluralize(count: number, singular: string, plural: string): string {
  return count === 1 ? singular : plural
}
