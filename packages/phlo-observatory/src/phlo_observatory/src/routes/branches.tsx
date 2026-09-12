/**
 * /branches route. Branch list and detail panels with branch actions routed
 * through a reducer; completed actions invalidate cached resources so new
 * refs show up immediately.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  AlertTriangle,
  Database,
  GitCompare,
  History,
  Plus,
  Table2,
} from 'lucide-react'
import { useCallback, useEffect, useReducer, useState } from 'react'

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
import {
  invalidateCachedResources,
  useLiveResource,
} from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

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

  const pageErrors = [
    detail.error,
    actionMessage,
    result.error,
    operationsResult.error,
    qualityResult.error,
  ].filter(Boolean)

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading
              ? 'Loading'
              : `${branches.length} ${pluralize(branches.length, 'branch', 'branches')}`}
          </Badge>
        }
        description="Review branch state, table drift, quality impact, and guarded change workflows."
        title="Change review"
      />
      <SplitView
        inspector={
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
        }
        inspectorWidth="w-[22rem]"
        list={
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
            <SectionCard
              actions={
                <Button
                  aria-expanded={branchDraftOpen}
                  onClick={() => setBranchDraftOpen((open) => !open)}
                  size="xs"
                  type="button"
                  variant="outline"
                >
                  <Plus className="size-3.5" />
                  Branch
                </Button>
              }
              title="Change reviews"
            >
              {branchDraftOpen && (
                <form
                  className="border-border flex flex-col gap-2 border-b px-3 py-2.5"
                  onSubmit={(event) => {
                    event.preventDefault()
                    createBranch()
                  }}
                >
                  <label className="flex flex-col gap-1">
                    <span className="text-muted-foreground text-[11px] font-medium">
                      Branch name
                    </span>
                    <Input
                      autoFocus
                      onChange={(event) =>
                        setBranchDraftName(event.target.value)
                      }
                      placeholder="review/revenue-fix"
                      value={branchDraftName}
                    />
                  </label>
                  <p className="text-muted-foreground text-[11px]/relaxed">
                    Creates review branch state through phlo-api, then opens the
                    new branch evidence.
                  </p>
                  <div className="flex items-center gap-2">
                    <Button
                      disabled={!branchDraftName.trim() || isCreatingBranch}
                      size="sm"
                      type="submit"
                    >
                      {isCreatingBranch ? 'Creating' : 'Create branch'}
                    </Button>
                    <Button
                      onClick={() => {
                        setBranchDraftName('')
                        setBranchDraftOpen(false)
                      }}
                      size="sm"
                      type="button"
                      variant="outline"
                    >
                      Cancel
                    </Button>
                  </div>
                </form>
              )}
              <ScrollArea className="max-h-64">
                <div className="divide-border divide-y">
                  {branches.map((branch) => (
                    <button
                      className={cn(
                        'hover:bg-accent/50 flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors',
                        branch.id === selected?.id &&
                          'bg-accent/60 hover:bg-accent/60',
                      )}
                      data-active={branch.id === selected?.id}
                      key={branch.id}
                      onClick={() => selectBranch(branch.id)}
                      type="button"
                    >
                      <div className="min-w-0">
                        <div className="text-foreground truncate text-xs font-medium">
                          {branch.name}
                        </div>
                        <div className="text-muted-foreground mt-0.5 font-mono text-[10px]">
                          {branch.current ? 'Current branch' : 'Review branch'}
                          {branchDelta(branch) && <> · {branchDelta(branch)}</>}
                        </div>
                      </div>
                      <Badge className="flex-none" variant="outline">
                        {branch.current
                          ? 'current'
                          : branch.protected
                            ? 'protected'
                            : 'branch'}
                      </Badge>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </SectionCard>
            {selected && (
              <>
                <SectionCard title={`Selected branch · ${selected.name}`}>
                  <div className="border-border flex flex-col gap-2 border-b px-3 py-3">
                    <p className="text-muted-foreground text-xs/relaxed">
                      {detail.data
                        ? branchNarrative(detail.data)
                        : branchNarrativeFromBranch(selected)}
                    </p>
                    <p className="text-muted-foreground font-mono text-[10px]">
                      {providerState}
                    </p>
                    <div
                      aria-label="Branch panels"
                      className="flex items-center gap-1.5 pt-1"
                    >
                      {(
                        [
                          ['compare', GitCompare, 'Compare'],
                          ['history', History, 'History'],
                          ['contents', Table2, 'Contents'],
                        ] as const
                      ).map(([panel, Icon, label]) => (
                        <Button
                          key={panel}
                          onClick={() =>
                            dispatch({ type: 'activePanel', panel })
                          }
                          size="xs"
                          type="button"
                          variant={
                            activePanel === panel ? 'default' : 'outline'
                          }
                        >
                          <Icon className="size-3.5" />
                          {label}
                        </Button>
                      ))}
                    </div>
                  </div>
                  <FactGrid className="grid-cols-6 p-3 max-lg:grid-cols-3">
                    <Fact label="Tables" value={selectedTableCount} />
                    <Fact label="Evidence" value={selectedEvidenceCount} />
                    <Fact
                      label="Blocking quality"
                      value={activeBlockingQuality}
                    />
                    <Fact label="Added" value={selectedCompare.added ?? 0} />
                    <Fact
                      label="Changed"
                      value={selectedCompare.changed ?? 0}
                    />
                    <Fact
                      label="Ahead / behind"
                      value={`${selectedCompare.ahead ?? 0} / ${selectedCompare.behind ?? 0}`}
                    />
                  </FactGrid>
                </SectionCard>
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
            {pageErrors.length > 0 && (
              <div className="flex flex-col gap-1">
                {pageErrors.map((message) => (
                  <p
                    className="text-status-error font-mono text-[10px] break-all"
                    key={message}
                  >
                    {message}
                  </p>
                ))}
              </div>
            )}
          </div>
        }
      />
    </Page>
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
    <section
      className="bg-card ring-foreground/10 flex flex-col ring-1"
      data-state={state}
    >
      <div className="border-border flex flex-wrap items-start justify-between gap-3 border-b px-3 py-3">
        <div>
          <span className="text-muted-foreground flex items-center gap-1.5 text-[9px] font-medium tracking-widest uppercase">
            <span className="status-dot" data-state={state} />
            Review state
          </span>
          <h3 className="text-foreground mt-0.5 text-sm font-semibold">
            {branch.current ? 'Protected baseline' : 'Review candidate'}
          </h3>
          <p className="text-muted-foreground mt-0.5 text-xs/relaxed">{next}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            nativeButton={false}
            render={
              <Link
                search={
                  primaryQuality ? { checkId: primaryQuality.id } : undefined
                }
                to="/quality"
              />
            }
            size="xs"
            variant="outline"
          >
            Quality
          </Button>
          <Button
            nativeButton={false}
            render={
              <Link
                search={
                  lineageAssetId ? { assetId: lineageAssetId } : undefined
                }
                to="/lineage"
              />
            }
            size="xs"
            variant="outline"
          >
            Lineage
          </Button>
          <Button
            nativeButton={false}
            render={
              <Link
                search={
                  (failedOperation ?? runningOperation)
                    ? {
                        operationId: (failedOperation ?? runningOperation)?.id,
                      }
                    : undefined
                }
                to="/operations"
              />
            }
            size="xs"
            variant="outline"
          >
            Operations
          </Button>
        </div>
      </div>
      <FactGrid className="grid-cols-4 p-3 max-lg:grid-cols-2">
        <Fact label="Tables in scope" value={tables.length} />
        <Fact label="Quality checks" value={quality.length} />
        <Fact label="Failing" value={failing.length} />
        <Fact
          label="Operation state"
          value={
            failedOperation ? 'failed' : runningOperation ? 'running' : 'clear'
          }
        />
      </FactGrid>
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
      <div className="flex flex-col gap-3">
        <StatGrid className="xl:grid-cols-3">
          <StatCard
            icon={<Plus className="size-3.5" />}
            label="Added"
            value={detail.compare.added ?? 0}
          />
          <StatCard
            icon={<GitCompare className="size-3.5" />}
            label="Changed"
            value={detail.compare.changed ?? 0}
          />
          <StatCard
            icon={<AlertTriangle className="size-3.5" />}
            label="Removed"
            state={(detail.compare.removed ?? 0) > 0 ? 'warning' : 'ok'}
            value={detail.compare.removed ?? 0}
          />
        </StatGrid>
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
      <SectionCard title="Branch history">
        <div className="divide-border divide-y">
          {commits.length > 0 ? (
            commits
              .slice(0, 8)
              .map((commit) => <CommitRow commit={commit} key={commit.id} />)
          ) : (
            <p className="text-muted-foreground px-3 py-2 text-xs">
              No operation evidence is linked to this branch yet.
            </p>
          )}
        </div>
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Branch contents">
      <div className="divide-border divide-y">
        {detail.tables.slice(0, 8).map((table) => (
          <TableRow key={table.id} table={table} />
        ))}
        {detail.tables.length === 0 && (
          <p className="text-muted-foreground px-3 py-2 text-xs">
            No branch contents yet.
          </p>
        )}
      </div>
    </SectionCard>
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
      <div className="flex flex-col gap-3">
        <StatGrid className="xl:grid-cols-3">
          <StatCard
            icon={<Plus className="size-3.5" />}
            label="Added"
            value={compare.added ?? 0}
          />
          <StatCard
            icon={<GitCompare className="size-3.5" />}
            label="Changed"
            value={compare.changed ?? 0}
          />
          <StatCard
            icon={<AlertTriangle className="size-3.5" />}
            label="Removed"
            state={(compare.removed ?? 0) > 0 ? 'warning' : 'ok'}
            value={compare.removed ?? 0}
          />
        </StatGrid>
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
      <SectionCard title="Branch history">
        <div className="divide-border divide-y">
          {operations.slice(0, 8).map((operation) => (
            <CommitRow commit={operation} key={operation.id} />
          ))}
        </div>
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Branch contents">
      <p className="text-muted-foreground px-3 py-2 text-xs">
        No branch contents are available yet. Branch operation evidence is shown
        above.
      </p>
    </SectionCard>
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
    <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
      <SectionCard title="Tables in scope">
        <div className="divide-border divide-y">
          {tables.length > 0 ? (
            tables
              .slice(0, 8)
              .map((table) => <TableRow key={table.id} table={table} />)
          ) : (
            <p className="text-muted-foreground px-3 py-2 text-xs">
              Branch evidence reported a changed table count, but the report did
              not include table evidence.
            </p>
          )}
        </div>
      </SectionCard>
      <SectionCard title="Impact">
        {quality.length > 0 ? (
          <div className="divide-border divide-y border-b">
            <p className="text-muted-foreground px-3 pt-2 text-[9px] font-medium tracking-widest uppercase">
              Quality impact
            </p>
            {quality.slice(0, 4).map((check) => (
              <Link
                className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
                key={check.id}
                to="/quality"
                search={{ checkId: check.id }}
              >
                <span className="text-foreground truncate text-[11px]">
                  {check.name}
                </span>
                <span className="text-muted-foreground flex-none font-mono text-[10px]">
                  {[qualityDatasetLabel(check), check.status, check.severity]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </Link>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground border-b px-3 py-2 text-xs">
            No quality checks are attached to the current branch contents.
          </p>
        )}
        <p className="text-muted-foreground px-3 pt-2 text-[9px] font-medium tracking-widest uppercase">
          Operation evidence
        </p>
        <div className="divide-border divide-y">
          {relatedOperations.length > 0 ? (
            relatedOperations
              .slice(0, 3)
              .map((operation) => (
                <CommitRow commit={operation} key={operation.id} />
              ))
          ) : (
            <p className="text-muted-foreground px-3 py-2 text-xs">
              No branch operation evidence is linked to {branch.name}.
            </p>
          )}
        </div>
      </SectionCard>
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
      <InspectorSection label="Review evidence">
        <p className="text-muted-foreground text-xs">
          {isLoading
            ? 'Reading branch state from the live lakehouse.'
            : 'Branch state appears once the live lakehouse API returns a branch.'}
        </p>
      </InspectorSection>
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
    <>
      <InspectorSection label={`Review evidence · ${branch.name}`}>
        <p className="text-muted-foreground text-xs/relaxed">
          {branch.current || branch.protected
            ? 'Protected baseline branch.'
            : 'Review branch awaiting approval.'}
        </p>
        <FactGrid>
          <Fact
            label="State"
            value={
              branch.protected
                ? 'protected'
                : branch.current
                  ? 'current'
                  : 'review'
            }
          />
          <Fact
            label="Tables"
            value={tables.length || metadataNumber(branch, 'tables')}
          />
          <Fact label="Changed" value={compare.changed ?? 0} />
          <Fact label="Blocking quality" value={failing.length} />
          <Fact label="Approval" value={approvalState} />
        </FactGrid>
      </InspectorSection>
      <InspectorSection label="Evidence">
        <div className="divide-border -mx-3 divide-y border-y">
          <MiniRow detail={providerState} label="Branch runtime" />
          {report ? (
            <CommitRow commit={report} />
          ) : operations.length > 0 ? (
            <MiniRow
              detail={[
                failed.length > 0 ? `${failed.length} failed` : null,
                running.length > 0 ? `${running.length} running` : null,
                operations.length > 0 ? `${operations.length} total` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
              label="Branch operation evidence"
            />
          ) : (
            <MiniRow
              detail="No operation evidence linked"
              label="Branch operation"
            />
          )}
          {failed.slice(0, 2).map((operation) => (
            <Link
              className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
              key={operation.id}
              search={{ operationId: operation.id }}
              to="/operations"
            >
              <span className="text-foreground truncate text-[11px]">
                {operation.name}
              </span>
              <span className="text-muted-foreground flex-none font-mono text-[10px]">
                {operation.health.message ?? operation.status}
              </span>
            </Link>
          ))}
          {failing.slice(0, 3).map((check) => (
            <Link
              className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
              key={check.id}
              search={{ checkId: check.id }}
              to="/quality"
            >
              <span className="text-foreground truncate text-[11px]">
                {check.name}
              </span>
              <span className="text-muted-foreground flex-none font-mono text-[10px]">
                {[qualityDatasetLabel(check), check.severity]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </Link>
          ))}
        </div>
      </InspectorSection>
    </>
  )
}

function MiniRow({ detail, label }: { detail: string; label: string }) {
  return (
    <div className="flex items-center justify-between gap-2 px-3 py-2">
      <span className="text-foreground text-[11px]">{label}</span>
      <span className="text-muted-foreground text-right font-mono text-[10px]">
        {detail}
      </span>
    </div>
  )
}

function TableRow({ table }: { table: ObservatoryTable }) {
  return (
    <Link
      className="hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors"
      search={{ tableId: table.id }}
      to="/tables"
    >
      <span className="text-foreground flex min-w-0 items-center gap-1.5 truncate text-[11px]">
        <Database className="size-3.5 flex-none" />
        {table.name}
      </span>
      <span className="text-muted-foreground flex-none font-mono text-[10px]">
        {[table.namespace, table.format, `${tableRecordCount(table)} records`]
          .filter(Boolean)
          .join(' · ')}
      </span>
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
      className={cn(
        'hover:bg-accent/50 flex items-center justify-between gap-2 px-3 py-2 transition-colors',
        commit.kind === 'wap' && 'bg-accent/30',
      )}
      to="/operations"
      search={{ operationId: commit.id }}
    >
      <span className="text-foreground min-w-0 truncate text-[11px]">
        {commit.name}
      </span>
      <span className="text-muted-foreground flex-none font-mono text-[10px]">
        {[
          commit.status,
          formatDateTime(commit.completed_at),
          hashMovement,
          reason,
        ]
          .filter(Boolean)
          .join(' · ')}
      </span>
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
