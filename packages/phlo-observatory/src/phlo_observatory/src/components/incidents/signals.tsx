/** Shows recorded incident measurements beside the live investigation. */
import { Link } from '@tanstack/react-router'
import { ExternalLinkIcon, GitBranchIcon } from 'lucide-react'
import { AuditRows, incidentFailureSql } from './audit-rows'
import {
  evidenceObject,
  incidentDuration,
  incidentEvidenceNumber,
} from './evidence'
import type { getIncidentDetail } from '@/lib/data/api/incidents'
import { Stat } from '@/components/phlo/kpi'
import { KeyValues } from '@/components/phlo/page'
import { Badge } from '@/components/ui/badge'

type Detail = Awaited<ReturnType<typeof getIncidentDetail>>

type SignalProps = Pick<Detail, 'incident' | 'runs' | 'investigation'> & {
  env: string
  evidence: Record<string, unknown>
}

export function IncidentSignals(props: SignalProps) {
  const kind = props.incident.kind.toLowerCase()
  if (/audit|check|quality/.test(kind)) return <AuditSignals {...props} />
  if (/performance|slow|duration/.test(kind))
    return <PerformanceSignals {...props} />
  if (/merge/.test(kind)) return <MergeSignals {...props} />
  if (/freshness/.test(kind)) return <FreshnessSignals {...props} />
  return null
}

function AuditSignals({ env, evidence, investigation, incident }: SignalProps) {
  const failing = incidentEvidenceNumber(evidence, 'failing_rows')
  const checked = incidentEvidenceNumber(evidence, 'rows_checked')
  const relation =
    'asset' in investigation ? investigation.asset.relation : undefined
  const sql = relation
    ? `SELECT *\nFROM ${relation
        .split('.')
        .map((part) => `"${part.replaceAll('"', '""')}"`)
        .join('.')}\nLIMIT 100`
    : undefined
  const failureSql =
    relation && 'asset' in investigation
      ? incidentFailureSql(
          relation,
          investigation.asset.columns ?? [],
          evidence.check,
        )
      : undefined
  return (
    <section aria-label="Recorded audit failure" className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat
          label="Rows failing"
          value={
            failing === undefined
              ? 'Not recorded'
              : checked === undefined
                ? failing
                : `${failing} of ${checked}`
          }
          tone={failing ? 'bad' : undefined}
          sub="At incident detection"
        />
        <Stat
          label="Audit"
          value={
            typeof evidence.check === 'string' ? evidence.check : 'Not recorded'
          }
          sub={
            typeof evidence.status === 'string' ? evidence.status : undefined
          }
        />
      </div>
      {failureSql ? (
        <AuditRows key={`${env}:${incident.id}`} env={env} sql={failureSql} />
      ) : (
        <div className="rounded-lg border border-line p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-sm font-medium">Contributing rows</h3>
            <Link
              to="/query"
              search={{ env, sql }}
              className="inline-flex items-center gap-1.5 text-sm"
            >
              <ExternalLinkIcon className="size-3.5" /> Open in Query
            </Link>
          </div>
          <p className="mb-0 text-sm leading-relaxed text-muted-foreground">
            The incident records audit counts, but no failing-row sample or
            plate attribution. Query the current table to investigate. Current
            rows may differ from the rows at detection.
          </p>
        </div>
      )}
    </section>
  )
}

function PerformanceSignals({ env, evidence, runs }: SignalProps) {
  const elapsed = incidentEvidenceNumber(evidence, 'elapsed_seconds')
  const threshold = incidentEvidenceNumber(
    evidence,
    'fixture_threshold_seconds',
  )
  const observed = runs.items
    .filter((run) => run.duration_seconds !== null)
    .sort(
      (a, b) =>
        Date.parse(a.started_at ?? a.created_at) -
        Date.parse(b.started_at ?? b.created_at),
    )
    .slice(-24)
  const max = Math.max(
    ...observed.map((run) => run.duration_seconds ?? 0),
    threshold ?? 0,
    1,
  )
  return (
    <section aria-label="Load performance" className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        <Stat
          label="Recorded duration"
          value={
            elapsed === undefined ? 'Not recorded' : incidentDuration(elapsed)
          }
          tone="warn"
          sub="Including orchestration"
        />
        <Stat
          label="Detection threshold"
          value={
            threshold === undefined
              ? 'Not recorded'
              : incidentDuration(threshold)
          }
          sub={
            typeof evidence.fixture === 'string'
              ? evidence.fixture
              : 'At incident detection'
          }
        />
      </div>
      <div className="rounded-lg border border-line p-4">
        <h3 className="m-0 text-sm font-medium">
          Run duration{' '}
          <span className="font-normal text-muted-foreground">
            · last {observed.length} observed runs
          </span>
        </h3>
        {observed.length ? (
          <div
            className="mt-5 flex h-40 items-end gap-2 border-b border-line pb-1"
            role="list"
            aria-label="Measured run durations"
          >
            {observed.map((run) => (
              <Link
                key={run.run_id}
                role="listitem"
                to="/pipelines/$jobName"
                params={{ jobName: run.job_id }}
                search={{ env, run: run.run_id }}
                title={`${run.job_id} · ${incidentDuration(run.duration_seconds ?? 0)} · ${run.run_id}`}
                className="group flex h-full min-w-0 flex-1 flex-col justify-end gap-1"
              >
                <span className="truncate text-center text-[10px] text-muted-foreground">
                  {incidentDuration(run.duration_seconds ?? 0)}
                </span>
                <span
                  className={
                    threshold !== undefined &&
                    (run.duration_seconds ?? 0) > threshold
                      ? 'min-h-1 rounded-t bg-warn transition-opacity group-hover:opacity-70'
                      : 'min-h-1 rounded-t bg-primary transition-opacity group-hover:opacity-70'
                  }
                  style={{
                    height: `${Math.max(2, ((run.duration_seconds ?? 0) / max) * 80)}%`,
                  }}
                />
              </Link>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No measured runs returned.
          </p>
        )}
        <p className="mb-0 text-xs text-muted-foreground">
          Measured durations in this environment. A historical baseline and step
          timing breakdown were not recorded.
        </p>
      </div>
      <Link
        to="/pipelines/timeline"
        search={{ env }}
        className="inline-flex items-center gap-2 text-sm"
      >
        <ExternalLinkIcon className="size-4" /> Open run timeline
      </Link>
    </section>
  )
}

function MergeSignals({ env, evidence, incident }: SignalProps) {
  const response = evidenceObject(evidence.response)
  const source =
    typeof evidence.source === 'string'
      ? evidence.source
      : typeof evidence.source_ref === 'string'
        ? evidence.source_ref
        : undefined
  const target =
    typeof evidence.target === 'string'
      ? evidence.target
      : typeof evidence.target_ref === 'string'
        ? evidence.target_ref
        : undefined
  return (
    <section aria-label="Merge conflict evidence" className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="m-0 text-[15px] font-medium">Branch merge blocked</h3>
        {typeof evidence.status_code === 'number' ? (
          <Badge variant="bad">HTTP {evidence.status_code}</Badge>
        ) : null}
      </div>
      <div className="overflow-hidden rounded-lg border border-line">
        <div className="grid grid-cols-2 border-b border-line bg-raised px-4 py-2 text-xs text-muted-foreground">
          <span>Target branch</span>
          <span>Source branch</span>
        </div>
        <div className="grid grid-cols-2 gap-3 px-4 py-3 text-sm">
          <div className="min-w-0">
            <p className="m-0 break-all font-mono">
              {target ?? 'Not recorded'}
            </p>
            <p
              className="mb-0 truncate font-mono text-xs text-muted-foreground"
              title={String(evidence.target_hash ?? '')}
            >
              {String(evidence.target_hash ?? 'Hash not recorded')}
            </p>
          </div>
          <div className="min-w-0">
            <p className="m-0 break-all font-mono">
              {source ?? 'Not recorded'}
            </p>
            <p
              className="mb-0 truncate font-mono text-xs text-muted-foreground"
              title={String(evidence.source_hash ?? '')}
            >
              {String(evidence.source_hash ?? 'Hash not recorded')}
            </p>
          </div>
        </div>
      </div>
      <KeyValues
        keyWidth={95}
        items={[
          [
            'Table',
            <span className="break-all font-mono text-xs">
              {incident.asset_id}
            </span>,
          ],
          [
            'Catalog result',
            typeof response.message === 'string'
              ? response.message
              : 'No conflict explanation recorded',
          ],
        ]}
      />
      <p className="rounded-lg border border-line bg-raised p-4 text-sm leading-relaxed text-muted-foreground">
        The recorded merge result identifies the conflicting table. Compare the
        branch revisions before choosing a resolution. Per-column differences
        and merge approval remain in Branches.
      </p>
      <Link
        to="/branches"
        search={{ env, branch: source }}
        className="inline-flex items-center gap-2 text-sm"
      >
        <GitBranchIcon className="size-4" /> Compare and resolve branch
      </Link>
    </section>
  )
}

function FreshnessSignals({ env, incident, investigation, runs }: SignalProps) {
  if (!('asset' in investigation)) return null
  const asset = investigation.asset
  const failed = runs.items.filter((run) => run.status === 'FAILURE')
  return (
    <section aria-label="Freshness impact" className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat
          label="Table freshness"
          value={asset.freshness_status ?? 'Unknown'}
          sub="Current materialization status"
          tone={asset.freshness_status === 'stale' ? 'bad' : undefined}
        />
        <Stat
          label="Downstream"
          value={asset.downstream?.length ?? 'Not recorded'}
          sub="Observed dependent assets"
        />
      </div>
      <div className="rounded-lg border border-line p-4">
        <h3 className="m-0 text-sm font-medium">What happened</h3>
        <p className="text-sm leading-relaxed text-text-3">
          {incident.description}
        </p>
        <p className="mb-0 text-xs leading-relaxed text-muted-foreground">
          Source-reading age and table materialization freshness are separate
          measurements. A new materialization can still contain old source
          readings. No historical freshness time series was recorded.
        </p>
      </div>
      {failed.length ? (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Failed ingestion runs</h3>
          {failed.map((run) => (
            <Link
              key={run.run_id}
              to="/pipelines/$jobName"
              params={{ jobName: run.job_id }}
              search={{ env, run: run.run_id }}
              className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-sm"
            >
              <span className="min-w-0 truncate font-mono">{run.job_id}</span>
              <Badge variant="bad">Failed</Badge>
            </Link>
          ))}
        </div>
      ) : null}
      <Link
        to="/assets/$assetId"
        params={{ assetId: incident.asset_id }}
        search={{ env, tab: 'lineage' }}
        className="inline-flex items-center gap-2 text-sm"
      >
        <ExternalLinkIcon className="size-4" /> Inspect downstream impact
      </Link>
    </section>
  )
}
