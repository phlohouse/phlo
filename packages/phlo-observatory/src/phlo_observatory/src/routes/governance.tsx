/**
 * /governance route. Renders the governance control matrix per dataset and
 * defaults selection to the first row with a failing control.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { FileCheck2, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type {
  ObservatoryControlStatus,
  ObservatoryGovernanceMatrix,
  ObservatoryGovernanceRow,
  ObservatoryResourceRef,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import { getObservatoryGovernanceItems } from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { loadCachedResource } from '@/observatory/routes/liveResource'

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
    <ObservatoryPage
      kicker="Governance"
      title="Dataset controls"
      description="Scan ownership, classification, and evidence-backed controls across Datasets."
      action={
        <span className="phlo-observatory-pill">
          {isLoading ? 'Loading' : `${rows.length} datasets`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-browser-toolbar">
            <div className="phlo-observatory-row-title">
              <ShieldCheck className="size-4" />
              Control matrix
            </div>
          </div>
          {result.error ? (
            <EmptyMatrix
              title="Governance could not load"
              detail={result.error}
            />
          ) : isLoading ? (
            <EmptyMatrix
              title="Loading Dataset controls"
              detail="Reading ownership, classifications, and control evidence from the active lakehouse."
            />
          ) : rows.length ? (
            <>
              <GovernanceSummary rows={rows} selected={selected} />
              <ControlMatrix
                onSelect={selectDataset}
                rows={rows}
                selectedId={selected?.dataset.id ?? null}
              />
            </>
          ) : (
            <EmptyMatrix
              title="No Dataset controls configured"
              detail="Create Datasets to populate the governance matrix."
            />
          )}
        </div>
        <GovernanceInspector isLoading={isLoading} row={selected} />
      </section>
    </ObservatoryPage>
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
    <div className="phlo-observatory-governance-summary">
      <SummaryCell
        label="Failed controls"
        state={failedControls ? 'error' : 'ok'}
        value={failedControls}
      />
      <SummaryCell
        label="Missing owners"
        state={missingOwners ? 'warning' : 'ok'}
        value={missingOwners}
      />
      <SummaryCell
        label="Missing classification"
        state={missingClassifications ? 'warning' : 'ok'}
        value={missingClassifications}
      />
      <SummaryCell
        label="Selected"
        state={selectedFailures.length ? 'error' : 'ok'}
        value={selected?.dataset.name ?? 'none'}
      />
    </div>
  )
}

function SummaryCell({
  label,
  state,
  value,
}: {
  label: string
  state: string
  value: string | number
}) {
  return (
    <div
      className="phlo-observatory-governance-summary-cell"
      data-state={state}
    >
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
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
    <div className="phlo-observatory-governance-matrix">
      <div className="phlo-observatory-governance-row phlo-observatory-governance-header">
        <span>Dataset</span>
        <span>Owner</span>
        <span>Classification</span>
        <span>Blocking quality</span>
        <span>Next action</span>
      </div>
      {rows.map((row) => {
        const owner = controlById(row, 'owner')
        const classification = controlById(row, 'classification')
        const blockingQuality = controlById(row, 'blocking_quality')
        return (
          <button
            className="phlo-observatory-governance-row"
            data-selected={selectedId === row.dataset.id}
            key={row.dataset.id}
            onClick={() => onSelect(row.dataset.id)}
            type="button"
          >
            <span>
              <strong>{row.dataset.name}</strong>
              <small>{row.dataset.publication_state}</small>
            </span>
            <ControlCell
              control={owner}
              fallback="unassigned"
              value={row.owner ?? 'unassigned'}
            />
            <ControlCell
              control={classification}
              fallback="missing"
              value={row.classifications.join(', ') || 'missing'}
            />
            <ControlCell
              control={blockingQuality}
              fallback="not reported"
              value={controlLabel(blockingQuality?.status ?? 'unknown')}
            />
            <span className="phlo-observatory-control-cell">
              {governanceNextAction(row)}
            </span>
          </button>
        )
      })}
    </div>
  )
}

function ControlCell({
  control,
  fallback,
  value,
}: {
  control: ObservatoryGovernanceRow['controls'][number] | undefined
  fallback: string
  value: string
}) {
  const status = control?.status ?? 'unknown'
  return (
    <span
      className="phlo-observatory-control-cell"
      data-label={control?.label ?? fallback}
    >
      <span
        className="phlo-observatory-dot"
        data-state={controlHealth(status)}
      />
      {value}
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
      <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
        <div className="phlo-observatory-inspector-label">Evidence</div>
        <h2>Loading controls</h2>
        <p>Control evidence appears once Dataset governance records load.</p>
      </aside>
    )
  }

  if (!row) {
    return (
      <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
        <div className="phlo-observatory-inspector-label">Evidence</div>
        <h2>No dataset selected</h2>
        <p>
          Select a dataset to inspect owners, classifications, controls, and
          evidence.
        </p>
      </aside>
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
    <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
      <div className="phlo-observatory-inspector-label">Evidence</div>
      <h2>{row.dataset.name}</h2>
      <p>{row.dataset.description ?? 'Control evidence for this Dataset.'}</p>
      <dl className="phlo-observatory-facts">
        <dt>Owner</dt>
        <dd>{row.owner ?? 'unassigned'}</dd>
        <dt>Classification</dt>
        <dd>{row.classifications.join(', ') || 'none'}</dd>
        <dt>Controls</dt>
        <dd>{controlSummary(row)}</dd>
        <dt>Evidence</dt>
        <dd>{evidenceCount}</dd>
      </dl>
      {nextActionHref ? (
        <Link
          className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
          data-state={controlHealth(row.status)}
          to={nextActionHref}
        >
          <span>Next action</span>
          <small>{nextAction}</small>
        </Link>
      ) : (
        <div
          className="phlo-observatory-mini-row"
          data-state={controlHealth(row.status)}
        >
          <span>Next action</span>
          <small>{nextAction}</small>
        </div>
      )}
      <div className="phlo-observatory-detail-list">
        {row.controls.map((control) => (
          <div
            className="phlo-observatory-governance-control-block"
            key={control.id}
          >
            <div
              className="phlo-observatory-mini-row"
              data-state={controlHealth(control.status)}
            >
              <span>{control.label}</span>
              <small>{controlLabel(control.status)}</small>
              <p>{control.message}</p>
            </div>
            {control.evidence.map((evidence) => (
              <EvidenceRow evidence={evidence} key={evidence.id} />
            ))}
            {control.evidence.length === 0 && (
              <div className="phlo-observatory-mini-row">
                <span>No evidence linked</span>
                <small>missing</small>
              </div>
            )}
          </div>
        ))}
        {failedControls.length === 0 &&
          warningControls.length === 0 &&
          unknownControls.length === 0 && (
            <div className="phlo-observatory-mini-row" data-state="ok">
              <span>Ready for publication controls</span>
              <small>no failing governance controls</small>
            </div>
          )}
      </div>
      <Link
        className="phlo-observatory-linked-resource"
        params={{ datasetId: row.dataset.id }}
        to="/datasets/$datasetId"
      >
        <FileCheck2 className="size-3.5" />
        Open Dataset
      </Link>
    </aside>
  )
}

function EmptyMatrix({ detail, title }: { detail: string; title: string }) {
  return (
    <div className="phlo-observatory-operation-empty">
      <div>
        <span className="phlo-observatory-inspector-label">Matrix</span>
        <h2>{title}</h2>
        <p>{detail}</p>
      </div>
    </div>
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
      <span>
        <FileCheck2 className="size-3.5" />
        {evidence.label}
      </span>
      <small>{evidence.value ?? evidence.kind}</small>
    </>
  )
  if (evidence.resource) {
    return (
      <Link
        className="phlo-observatory-mini-row phlo-observatory-linked-mini-row"
        to={resourceHref(evidence.resource)}
      >
        {content}
      </Link>
    )
  }
  return <div className="phlo-observatory-mini-row">{content}</div>
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
