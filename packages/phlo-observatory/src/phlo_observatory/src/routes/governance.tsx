/**
 * /governance route. Renders the governance control matrix per dataset and
 * defaults selection to the first row with a failing control.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { FileCheck2, ShieldAlert, ShieldCheck, Tag, User } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ObservatoryControlStatus,
  ObservatoryGovernanceMatrix,
  ObservatoryGovernanceRow,
  ObservatoryResourceRef,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import { getObservatoryGovernanceItems } from '@/observatory/api/resources'
import { loadCachedResource } from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import {
  EmptyBlock,
  ErrorBlock,
  LoadingBlock,
} from '@/components/observatory/states'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { HealthDot, StatusBadge } from '@/components/observatory/status'
import { StatCard, StatGrid } from '@/components/observatory/stat'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/governance')({
  component: Governance,
})

export function Governance() {
  const [result, setResult] = useState<
    ObservatoryResourceResult<ObservatoryGovernanceMatrix>
  >({ data: null, error: null })
  const matrix = result.data
  const isLoading = matrix === null && !result.error
  const rows = matrix?.rows ?? []
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = useMemo(
    () =>
      rows.find((row) => row.dataset.id === selectedId) ??
      rows.find((row) =>
        row.controls.some((control) => control.status === 'fail'),
      ) ??
      rows[0] ??
      null,
    [rows, selectedId],
  )
  const selectDataset = useCallback((datasetId: string) => {
    setSelectedId(datasetId)
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('datasetId', datasetId)
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [])

  useEffect(() => {
    let cancelled = false
    void loadCachedResource(
      'observatory:governance-matrix',
      getObservatoryGovernanceItems,
      {
        force: true,
        staleMs: 120_000,
      },
    ).then((next) => {
      if (!cancelled) setResult(next)
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get(
      'datasetId',
    )
    if (!requested || requested === selectedId) return
    if (rows.some((row) => row.dataset.id === requested)) {
      setSelectedId(requested)
    }
  }, [rows, selectedId])

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (!selected || selectedId !== null) return
    const requested = new URLSearchParams(window.location.search).get(
      'datasetId',
    )
    if (requested && rows.some((row) => row.dataset.id === requested)) return
    if (requested === selected.dataset.id) return
    selectDataset(selected.dataset.id)
  }, [rows, selectDataset, selected, selectedId])

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {isLoading ? 'Loading' : `${rows.length} datasets`}
          </Badge>
        }
        description="Scan ownership, classification, and evidence-backed controls across Datasets."
        title="Dataset controls"
      />
      <GovernanceSummary rows={rows} selected={selected} />
      <SplitView
        inspector={<GovernanceInspector isLoading={isLoading} row={selected} />}
        inspectorWidth="w-[24rem]"
        list={
          <SectionCard className="ring-0" title="Control matrix">
            {result.error ? (
              <ErrorBlock
                error={result.error}
                title="Governance could not load"
              />
            ) : isLoading ? (
              <LoadingBlock className="p-3" label="Loading Dataset controls" />
            ) : rows.length ? (
              <ControlMatrix
                onSelect={selectDataset}
                rows={rows}
                selectedId={selected?.dataset.id ?? null}
              />
            ) : (
              <EmptyBlock
                description="Create Datasets to populate the governance matrix."
                title="No Dataset controls configured"
              />
            )}
          </SectionCard>
        }
      />
    </Page>
  )
}

function GovernanceSummary({
  rows,
  selected,
}: {
  rows: Array<ObservatoryGovernanceRow>
  selected: ObservatoryGovernanceRow | null
}) {
  const failedControls = rows.reduce(
    (total, row) =>
      total +
      row.controls.filter((control) => control.status === 'fail').length,
    0,
  )
  const missingOwners = rows.filter((row) => !row.owner).length
  const missingClassifications = rows.filter(
    (row) => row.classifications.length === 0,
  ).length
  const selectedFailures =
    selected?.controls.filter((control) => control.status === 'fail') ?? []
  return (
    <StatGrid>
      <StatCard
        icon={<ShieldAlert className="size-3.5" />}
        label="Failed controls"
        state={failedControls ? 'error' : 'ok'}
        value={failedControls}
      />
      <StatCard
        icon={<User className="size-3.5" />}
        label="Missing owners"
        state={missingOwners ? 'warning' : 'ok'}
        value={missingOwners}
      />
      <StatCard
        icon={<Tag className="size-3.5" />}
        label="Missing classification"
        state={missingClassifications ? 'warning' : 'ok'}
        value={missingClassifications}
      />
      <StatCard
        icon={<ShieldCheck className="size-3.5" />}
        label="Selected"
        state={selectedFailures.length ? 'error' : 'ok'}
        value={selected?.dataset.name ?? 'none'}
      />
    </StatGrid>
  )
}

function ControlMatrix({
  onSelect,
  rows,
  selectedId,
}: {
  onSelect: (id: string) => void
  rows: Array<ObservatoryGovernanceRow>
  selectedId: string | null
}) {
  return (
    <div>
      <div className="text-muted-foreground grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,1.2fr)] gap-3 border-b px-3 py-1.5 font-mono text-[9px] font-medium tracking-widest uppercase">
        <span>Dataset</span>
        <span>Owner</span>
        <span>Classification</span>
        <span>Blocking quality</span>
        <span>Next action</span>
      </div>
      <div className="divide-border divide-y">
        {rows.map((row) => {
          const owner = controlById(row, 'owner')
          const classification = controlById(row, 'classification')
          const blockingQuality = controlById(row, 'blocking_quality')
          return (
            <button
              className={cn(
                'hover:bg-accent/50 grid w-full grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,1.2fr)] items-center gap-3 px-3 py-2 text-left transition-colors',
                selectedId === row.dataset.id &&
                  'bg-accent/60 hover:bg-accent/60',
              )}
              key={row.dataset.id}
              onClick={() => onSelect(row.dataset.id)}
              type="button"
            >
              <span className="min-w-0">
                <span className="text-foreground block truncate text-xs font-medium">
                  {row.dataset.name}
                </span>
                <span className="text-muted-foreground block truncate font-mono text-[10px]">
                  {row.dataset.publication_state}
                </span>
              </span>
              <ControlCell control={owner} value={row.owner ?? 'unassigned'} />
              <ControlCell
                control={classification}
                value={row.classifications.join(', ') || 'missing'}
              />
              <ControlCell
                control={blockingQuality}
                value={controlLabel(blockingQuality?.status ?? 'unknown')}
              />
              <span className="text-muted-foreground truncate font-mono text-[10px]">
                {governanceNextAction(row)}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function ControlCell({
  control,
  value,
}: {
  control: ObservatoryGovernanceRow['controls'][number] | undefined
  value: string
}) {
  const status = control?.status ?? 'unknown'
  return (
    <span className="flex min-w-0 items-center gap-1.5" title={control?.label}>
      <HealthDot state={controlHealth(status)} />
      <span className="text-muted-foreground truncate font-mono text-[10px]">
        {value}
      </span>
    </span>
  )
}

function GovernanceInspector({
  isLoading,
  row,
}: {
  isLoading: boolean
  row: ObservatoryGovernanceRow | null
}) {
  if (isLoading) {
    return (
      <InspectorSection label="Evidence">
        <p className="text-muted-foreground text-xs">
          Control evidence appears once Dataset governance records load.
        </p>
      </InspectorSection>
    )
  }

  if (!row) {
    return (
      <InspectorSection label="Evidence">
        <p className="text-muted-foreground text-xs">
          Select a dataset to inspect owners, classifications, controls, and
          evidence.
        </p>
      </InspectorSection>
    )
  }

  const evidenceCount = row.controls.reduce(
    (count, control) => count + control.evidence.length,
    0,
  )
  const failedControls = row.controls.filter(
    (control) => control.status === 'fail',
  )
  const warningControls = row.controls.filter(
    (control) => control.status === 'warning',
  )
  const unknownControls = row.controls.filter(
    (control) => control.status === 'unknown',
  )
  const nextAction = governanceNextAction(row)
  const nextActionHref = governanceNextActionHref(row)
  return (
    <>
      <InspectorSection label="Evidence">
        <div className="flex items-center gap-2">
          <span className="text-foreground text-xs font-semibold">
            {row.dataset.name}
          </span>
          <StatusBadge
            label={controlLabel(row.status)}
            state={controlHealth(row.status)}
          />
        </div>
        <p className="text-muted-foreground text-xs/relaxed">
          {row.dataset.description ?? 'Control evidence for this Dataset.'}
        </p>
        <FactGrid>
          <Fact label="Owner" value={row.owner ?? 'unassigned'} />
          <Fact
            label="Classification"
            value={row.classifications.join(', ') || 'none'}
          />
          <Fact label="Controls" value={controlSummary(row)} />
          <Fact label="Evidence" value={evidenceCount} />
        </FactGrid>
      </InspectorSection>
      <InspectorSection label="Next action">
        {nextActionHref ? (
          <Link
            className="hover:bg-accent/50 flex items-start gap-2 border-y py-2 transition-colors"
            to={nextActionHref}
          >
            <HealthDot className="mt-1" state={controlHealth(row.status)} />
            <span className="min-w-0">
              <span className="text-foreground block text-[11px]">
                Next action
              </span>
              <span className="text-muted-foreground block font-mono text-[10px]">
                {nextAction}
              </span>
            </span>
          </Link>
        ) : (
          <div className="flex items-start gap-2 border-y py-2">
            <HealthDot className="mt-1" state={controlHealth(row.status)} />
            <span className="min-w-0">
              <span className="text-foreground block text-[11px]">
                Next action
              </span>
              <span className="text-muted-foreground block font-mono text-[10px]">
                {nextAction}
              </span>
            </span>
          </div>
        )}
      </InspectorSection>
      <InspectorSection label="Controls">
        <div className="divide-border divide-y border-y">
          {row.controls.map((control) => (
            <div className="py-2" key={control.id}>
              <div className="flex items-start gap-2">
                <HealthDot
                  className="mt-1"
                  state={controlHealth(control.status)}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-foreground text-[11px]">
                      {control.label}
                    </span>
                    <span className="text-muted-foreground font-mono text-[10px]">
                      {controlLabel(control.status)}
                    </span>
                  </span>
                  {control.message && (
                    <span className="text-muted-foreground mt-0.5 block text-[10px]/relaxed">
                      {control.message}
                    </span>
                  )}
                </span>
              </div>
              <div className="mt-1 ml-4 flex flex-col gap-1">
                {control.evidence.map((evidence) => (
                  <EvidenceRow evidence={evidence} key={evidence.id} />
                ))}
                {control.evidence.length === 0 && (
                  <span className="text-muted-foreground font-mono text-[10px]">
                    No evidence linked · missing
                  </span>
                )}
              </div>
            </div>
          ))}
          {failedControls.length === 0 &&
            warningControls.length === 0 &&
            unknownControls.length === 0 && (
              <div className="flex items-center gap-2 py-2">
                <HealthDot state="ok" />
                <span className="text-foreground text-[11px]">
                  Ready for publication controls
                </span>
                <span className="text-muted-foreground font-mono text-[10px]">
                  no failing governance controls
                </span>
              </div>
            )}
        </div>
      </InspectorSection>
      <InspectorSection label="Dataset">
        <Link
          className="hover:bg-accent/50 text-foreground flex items-center gap-1.5 border-y py-2 text-[11px] transition-colors"
          params={{ datasetId: row.dataset.id }}
          to="/datasets/$datasetId"
        >
          <FileCheck2 className="size-3.5" />
          Open Dataset
        </Link>
      </InspectorSection>
    </>
  )
}

function controlHealth(status: ObservatoryControlStatus) {
  if (status === 'pass') return 'ok'
  if (status === 'fail') return 'error'
  if (status === 'warning') return 'warning'
  return 'unknown'
}

function controlLabel(status: ObservatoryControlStatus) {
  return status.replace('_', ' ')
}

function controlSummary(row: ObservatoryGovernanceRow): string {
  const failing = row.controls.filter(
    (control) => control.status === 'fail',
  ).length
  const warnings = row.controls.filter(
    (control) => control.status === 'warning',
  ).length
  if (failing > 0) return `${failing} failing`
  if (warnings > 0) return `${warnings} warning`
  return 'clear'
}

/**
 * Canonical-only next action: derived from the server-rendered
 * control verdicts in the matrix, never re-inferred from owner or
 * classification fields.
 */
function governanceNextAction(row: ObservatoryGovernanceRow): string {
  const failing = row.controls.find((control) => control.status === 'fail')
  if (failing) {
    return failing.message ?? `Resolve the failing ${failing.label} control.`
  }
  if (
    row.controls.some(
      (control) => control.status === 'warning' || control.status === 'unknown',
    )
  ) {
    return 'Review missing evidence.'
  }
  return 'No governance action required.'
}

function governanceNextActionHref(
  row: ObservatoryGovernanceRow,
): string | null {
  const blockingQuality = controlById(row, 'blocking_quality')
  const linkedCheck = blockingQuality?.evidence.find(
    (evidence) => evidence.resource?.kind === 'quality',
  )?.resource
  if (blockingQuality?.status === 'fail' && linkedCheck) {
    return resourceHref(linkedCheck)
  }
  return null
}

function controlById(row: ObservatoryGovernanceRow, id: string) {
  return row.controls.find((control) => control.id === id)
}

function EvidenceRow({
  evidence,
}: {
  evidence: ObservatoryGovernanceRow['controls'][number]['evidence'][number]
}) {
  const content = (
    <>
      <span className="text-foreground flex items-center gap-1.5 text-[11px]">
        <FileCheck2 className="size-3" />
        {evidence.label}
      </span>
      <span className="text-muted-foreground font-mono text-[10px]">
        {evidence.value ?? evidence.kind}
      </span>
    </>
  )
  if (evidence.resource) {
    return (
      <Link
        className="hover:bg-accent/50 flex items-center justify-between gap-2 py-1 transition-colors"
        to={resourceHref(evidence.resource)}
      >
        {content}
      </Link>
    )
  }
  return (
    <div className="flex items-center justify-between gap-2 py-1">
      {content}
    </div>
  )
}

function resourceHref(resource: ObservatoryResourceRef): string {
  if (resource.kind === 'dataset') {
    return `/datasets/${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'quality') {
    return `/quality?checkId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'table') {
    return `/tables?tableId=${encodeURIComponent(resource.id)}`
  }
  if (resource.kind === 'asset') {
    return `/lineage?assetId=${encodeURIComponent(resource.id)}`
  }
  return `/datasets/${encodeURIComponent(resource.id)}`
}
