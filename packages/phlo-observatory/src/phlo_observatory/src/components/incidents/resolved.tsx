/** Renders resolved incident details, timeline events, and follow-ups. */
import * as React from 'react'
import { Link, useRouter } from '@tanstack/react-router'
import { GitBranchIcon } from 'lucide-react'
import type {
  IncidentFollowUp,
  IncidentRecord,
  IncidentTimelineEvent,
  getIncidentDetail,
} from '@/lib/data/api/incidents'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input, Textarea } from '@/components/ui/input'
import { EmptyState } from '@/components/phlo/states'
import { Eyebrow, KeyValues } from '@/components/phlo/page'
import { Stat } from '@/components/phlo/kpi'
import { Mono } from '@/components/phlo/status'
import { Card } from '@/components/ui/card'
import { SchemaDecisions } from '@/components/incidents/schema-decisions'
import {
  clearIncidentOperationKey,
  createFollowUp,
  incidentOperationKey,
  updateFollowUp,
  updateIncident,
} from '@/lib/data/api/incidents'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

const formatDate = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
const payloadText = (payload: IncidentTimelineEvent['payload']) => {
  if (typeof payload.text === 'string') return payload.text
  return Object.keys(payload).length
    ? JSON.stringify(payload, null, 2)
    : undefined
}
function signalEvidence(timeline: Array<IncidentTimelineEvent>) {
  const signal = timeline.find((event) => event.kind === 'signal')?.payload
    .evidence
  return typeof signal === 'object' && signal !== null && !Array.isArray(signal)
    ? signal
    : {}
}
const evidenceValue = (
  timeline: Array<IncidentTimelineEvent>,
  keys: Array<string>,
) => {
  for (const event of timeline) {
    for (const key of keys) {
      const value = event.payload[key]
      if (typeof value === 'string' && value.trim()) return value
      const evidence = event.payload.evidence
      if (
        typeof evidence === 'object' &&
        evidence !== null &&
        !Array.isArray(evidence)
      ) {
        const nested = evidence[key]
        if (typeof nested === 'string' && nested.trim()) return nested
      }
    }
  }
}

function linkedIncidentEvidence(
  timeline: Array<IncidentTimelineEvent>,
  env: 'prod' | 'staging',
) {
  const branch = evidenceValue(timeline, ['branch', 'branch_name', 'ref'])
  const job = evidenceValue(timeline, ['job', 'job_name', 'pipeline'])
  const items: Array<[React.ReactNode, React.ReactNode]> = []
  if (branch)
    items.push([
      'Branch',
      <Link
        to="/branches"
        search={{ env, branch }}
        className="min-w-0 truncate font-mono text-xs"
      >
        <GitBranchIcon className="mr-1 inline size-3.5" />
        {branch}
      </Link>,
    ])
  if (job)
    items.push([
      'Job',
      <Link
        to="/pipelines/$jobName"
        params={{ jobName: job }}
        search={{ env }}
        className="min-w-0 truncate font-mono text-xs"
      >
        {job}
      </Link>,
    ])
  return items
}

function resolutionLabel(
  incident: IncidentRecord,
  timeline: Array<IncidentTimelineEvent>,
) {
  const resolvedAt = [...timeline]
    .reverse()
    .find((event) => event.kind.toLowerCase().includes('resolv'))?.occurred_at
  if (resolvedAt) return formatDate(resolvedAt)
  return incident.status === 'resolved'
    ? 'Recorded without a resolution timestamp'
    : 'Not resolved'
}

function IncidentSummary({
  env,
  incident,
  timeline,
  followUps,
  linkedEvidence,
  owner,
  comment,
  pending,
  error,
  onOwnerChange,
  onCommentChange,
  onSaveOwner,
  onAddComment,
}: {
  env: 'prod' | 'staging'
  incident: IncidentRecord
  timeline: Array<IncidentTimelineEvent>
  followUps: Array<IncidentFollowUp>
  linkedEvidence: Array<[React.ReactNode, React.ReactNode]>
  owner: string
  comment: string
  pending: boolean
  error?: string
  onOwnerChange: (value: string) => void
  onCommentChange: (value: string) => void
  onSaveOwner: () => void
  onAddComment: () => void
}) {
  return (
    <aside
      aria-label="Summary"
      className="border-b border-line lg:overflow-y-auto lg:border-r lg:border-b-0"
    >
      <div className="flex flex-col gap-2.5 px-4 pt-5 pb-4 lg:px-6 lg:pt-[22px]">
        <div className="flex flex-wrap items-center gap-2">
          <Mono className="text-[13px] text-muted-foreground">
            #{incident.id}
          </Mono>
          <Badge
            variant={
              incident.status === 'resolved'
                ? 'ok'
                : incident.status === 'acknowledged'
                  ? 'warn'
                  : 'bad'
            }
          >
            {incident.status}
          </Badge>
          <Badge variant="outline">{incident.kind}</Badge>
          <Badge
            variant={
              incident.severity === 'high'
                ? 'bad'
                : incident.severity === 'medium'
                  ? 'warn'
                  : 'outline'
            }
          >
            {incident.severity}
          </Badge>
        </div>
        <h2 className="m-0 text-[22px] leading-tight font-semibold tracking-[-0.01em]">
          {incident.title}
        </h2>
        <p className="m-0 text-sm leading-relaxed text-text-3">
          {incident.description ||
            (incident.status === 'resolved'
              ? 'This incident is resolved. Persisted evidence and the audit trail are shown here.'
              : 'Investigation is active. This view only shows evidence persisted by the incident service.')}
        </p>
      </div>
      <div className="border-t border-line-soft px-4 py-4 lg:px-6">
        <KeyValues
          keyWidth={90}
          className="items-center gap-y-3"
          items={[
            [
              'Asset',
              <span className="flex flex-col gap-1">
                {(incident.asset_ids.length
                  ? incident.asset_ids
                  : [incident.asset_id]
                ).map((asset) => (
                  <Link
                    key={asset}
                    to="/assets/$assetId"
                    params={{ assetId: asset }}
                    search={{ env }}
                    className="break-all font-mono text-xs"
                  >
                    {asset}
                  </Link>
                ))}
              </span>,
            ],
            ['Owner', incident.owner ?? 'Unassigned'],
            ['Created', formatDate(incident.created_at)],
            ['Updated', formatDate(incident.updated_at)],
            ['Version', String(incident.version)],
            ...linkedEvidence,
          ]}
        />
      </div>
      <div className="grid grid-cols-2 gap-2.5 border-t border-line-soft px-4 py-4 lg:px-6">
        <Stat label="Activity" value={timeline.length} sub="persisted events" />
        <Stat
          label="Follow-ups"
          value={`${followUps.filter((item) => item.completed_at).length}/${followUps.length}`}
          sub="complete"
          tone={followUps.some((item) => !item.completed_at) ? 'warn' : 'ok'}
        />
      </div>
      <div className="flex flex-col gap-3 border-t border-line px-4 py-5 lg:px-6">
        <Eyebrow>Update incident</Eyebrow>
        <label className="text-sm">
          Owner
          <Input
            className="mt-1.5"
            value={owner}
            maxLength={512}
            onChange={(event) => onOwnerChange(event.target.value)}
          />
        </label>
        <Button
          variant="outline"
          disabled={pending || owner === (incident.owner ?? '')}
          onClick={onSaveOwner}
        >
          Save owner
        </Button>
        <label className="text-sm">
          Comment
          <Textarea
            className="mt-1.5"
            rows={3}
            value={comment}
            onChange={(event) => onCommentChange(event.target.value)}
          />
        </label>
        <Button disabled={pending || !comment.trim()} onClick={onAddComment}>
          Add comment
        </Button>
        {error ? (
          <p role="alert" className="m-0 text-sm text-bad-text">
            {error}
          </p>
        ) : null}
      </div>
    </aside>
  )
}

function FreshnessInvestigation({
  incident,
  timeline,
  investigation,
  evidence,
}: Pick<
  Awaited<ReturnType<typeof getIncidentDetail>>,
  'incident' | 'timeline' | 'investigation'
> & {
  evidence: IncidentTimelineEvent['payload']
}) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <Stat
          label="Last success"
          value={
            'asset' in investigation &&
            investigation.asset.last_materialization_at
              ? formatDate(investigation.asset.last_materialization_at)
              : 'Unknown'
          }
        />
        <Stat
          label="Freshness SLA"
          value={
            typeof evidence.freshness_sla_seconds === 'number'
              ? `${evidence.freshness_sla_seconds / 60} min`
              : 'Not configured'
          }
        />
      </div>
      <Card className="p-4">
        <KeyValues
          keyWidth={140}
          items={[
            ['Detected at', formatDate(incident.created_at)],
            [
              'Last successful run',
              typeof evidence.successful_run_id === 'string'
                ? evidence.successful_run_id
                : 'Not recorded',
            ],
            [
              'Materialization event',
              typeof evidence.materialization_event_id === 'number'
                ? String(evidence.materialization_event_id)
                : 'Not recorded',
            ],
            ['Resolution', resolutionLabel(incident, timeline)],
          ]}
        />
      </Card>
      <p className="m-0 text-sm text-text-3">
        Check ingestion failures against the freshness SLA before retrying. A
        recent success does not erase the original breach evidence.
      </p>
    </>
  )
}

function IncidentInvestigation({
  env,
  incident,
  timeline,
  investigation,
}: Pick<
  Awaited<ReturnType<typeof getIncidentDetail>>,
  'incident' | 'timeline' | 'investigation'
> & { env: 'prod' | 'staging' }) {
  const kind = incident.kind.toLowerCase()
  const heading = kind.includes('freshness')
    ? 'Freshness investigation'
    : kind.includes('schema')
      ? 'Schema investigation'
      : /audit|check|quality/.test(kind)
        ? 'Audit investigation'
        : 'Investigation & evidence'
  const evidence = signalEvidence(timeline)
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="m-0 text-[15px] font-medium">{heading}</h2>
        <p className="m-0 mt-1 text-[13px] text-muted-foreground">
          Observed asset state and persisted incident evidence in {env}.
        </p>
      </div>
      {investigation.kind === 'unavailable' ? (
        <EmptyState title={`${heading} unavailable`}>
          {investigation.message}
        </EmptyState>
      ) : null}
      {kind.includes('freshness') ? (
        <FreshnessInvestigation
          incident={incident}
          timeline={timeline}
          investigation={investigation}
          evidence={evidence}
        />
      ) : null}
      {investigation.kind === 'schema' ? (
        <>
          <div className="flex items-center gap-2">
            <h3 className="m-0 text-sm font-medium">Schema history</h3>
            <Badge variant="outline">
              Current #{investigation.data.current_schema_id}
            </Badge>
          </div>
          {investigation.data.items.map((schema) => (
            <div
              key={schema.schema_id}
              className="overflow-x-auto rounded-lg border border-border-card"
            >
              <Table>
                <caption className="px-3 py-2 text-left text-sm">
                  Schema #{schema.schema_id}
                  {schema.schema_id === investigation.data.current_schema_id
                    ? ' · current'
                    : ''}
                </caption>
                <TableHeader>
                  <TableRow>
                    <TableHead>Column</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Required</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {schema.fields.map((field) => (
                    <TableRow key={field.name}>
                      <TableCell className="font-mono">{field.name}</TableCell>
                      <TableCell className="font-mono">{field.type}</TableCell>
                      <TableCell>{field.required ? 'Yes' : 'No'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ))}
          <p className="m-0 text-sm text-muted-foreground">
            Branch schema decisions remain bound to source and target hashes.
            Merge requires checks, a fresh signature, and MFA.
          </p>
          <Link
            to="/branches"
            search={{
              env,
              branch: evidenceValue(timeline, ['source_ref', 'branch']),
            }}
            className="text-sm"
          >
            Compare and resolve in Branches
          </Link>
        </>
      ) : null}
      {investigation.kind === 'audits' ? (
        <>
          <h3 className="m-0 text-sm font-medium">Audit results</h3>
          {investigation.data.executions.length ? (
            <div className="overflow-x-auto rounded-lg border border-border-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Check</TableHead>
                    <TableHead>Result</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>Observed</TableHead>
                    <TableHead>Run</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {investigation.data.executions.map((check, index) => (
                    <TableRow
                      key={`${check.run_id}:${check.check_name}:${index}`}
                    >
                      <TableCell>{check.check_name}</TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            check.passed === true
                              ? 'ok'
                              : check.passed === false
                                ? 'bad'
                                : 'outline'
                          }
                        >
                          {check.passed === true
                            ? 'Passed'
                            : check.passed === false
                              ? 'Failed'
                              : check.status}
                        </Badge>
                      </TableCell>
                      <TableCell>{check.severity ?? 'Unknown'}</TableCell>
                      <TableCell>{formatDate(check.timestamp)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {check.run_id}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <EmptyState title="No audit executions">
              {investigation.data.definitions.length} check definitions, but no
              execution evidence was returned.
            </EmptyState>
          )}
          <p className="m-0 text-sm text-text-3">
            Query contributing data, then pin the completed execution to retain
            its statement and result identity without exposing rows to incident
            readers.
          </p>
          <Link to="/query" search={{ env }} className="text-sm">
            Open Query
          </Link>
        </>
      ) : null}
      {'jobs' in investigation && investigation.jobs.length ? (
        <div className="flex flex-wrap gap-2">
          {investigation.jobs.map((job) => (
            <Link
              key={job.id}
              to="/pipelines/$jobName"
              params={{ jobName: job.id }}
              search={{ env }}
              className="text-sm"
            >
              Open {job.id} in Jobs
            </Link>
          ))}
        </div>
      ) : null}
      <div className="flex flex-col gap-2">
        <h3 className="m-0 text-sm font-medium">Pinned query evidence</h3>
        {timeline
          .filter((event) => event.kind === 'query_evidence')
          .map((event) => (
            <Card key={event.id} className="overflow-hidden p-3">
              <KeyValues
                keyWidth={90}
                items={Object.entries(event.payload).map(([key, value]) => [
                  key.replaceAll('_', ' '),
                  <span className="break-all font-mono text-xs">
                    {String(value ?? '—')}
                  </span>,
                ])}
              />
            </Card>
          ))}
        {!timeline.some((event) => event.kind === 'query_evidence') ? (
          <p className="m-0 text-sm text-muted-foreground">
            No completed query executions pinned.
          </p>
        ) : null}
      </div>
    </section>
  )
}

function IncidentRuns({
  env,
  runs,
}: Pick<Awaited<ReturnType<typeof getIncidentDetail>>, 'runs'> & {
  env: 'prod' | 'staging'
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="m-0 text-[15px] font-medium">Runs for affected assets</h2>
      <p className="m-0 text-xs text-muted-foreground">
        Observed in {env}
        {runs.truncated
          ? '. History is bounded; older runs may be missing.'
          : '.'}
      </p>
      {runs.error ? (
        <EmptyState title="Runs unavailable">{runs.error}</EmptyState>
      ) : runs.items.length ? (
        <div
          role="region"
          aria-label="Incident run history"
          tabIndex={0}
          className="overflow-x-auto rounded-lg border border-border-card focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <Table className="min-w-[620px] whitespace-nowrap">
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Job</TableHead>
                <TableHead>Started</TableHead>
                <TableHead>Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.items.map((run) => (
                <TableRow key={run.run_id}>
                  <TableCell>
                    <Link
                      to="/pipelines/$jobName"
                      params={{ jobName: run.job_id }}
                      search={{ env, run: run.run_id }}
                      className="font-mono text-xs"
                    >
                      {run.run_id}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        run.status === 'SUCCESS'
                          ? 'ok'
                          : run.status === 'FAILURE'
                            ? 'bad'
                            : 'outline'
                      }
                    >
                      {run.status}
                    </Badge>
                  </TableCell>
                  <TableCell>{run.job_id}</TableCell>
                  <TableCell>
                    {formatDate(run.started_at ?? run.created_at)}
                  </TableCell>
                  <TableCell>
                    {run.duration_seconds === null
                      ? '—'
                      : `${run.duration_seconds.toFixed(1)} s`}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <EmptyState title="No related runs observed">
          No affected-asset runs were returned in the bounded environment
          history.
        </EmptyState>
      )}
    </section>
  )
}

export function IncidentDetail({
  env,
  incident,
  timeline,
  followUps,
  runs,
  investigation,
}: Awaited<ReturnType<typeof getIncidentDetail>> & {
  env: 'prod' | 'staging'
}) {
  const router = useRouter()
  const [comment, setComment] = React.useState('')
  const [owner, setOwner] = React.useState(incident.owner ?? '')
  const [followUp, setFollowUp] = React.useState('')
  const [due, setDue] = React.useState('')
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const [tab, setTab] = React.useState<
    'summary' | 'postmortem' | 'activity' | 'runs' | 'lineage'
  >(incident.status === 'resolved' ? 'postmortem' : 'summary')
  const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([])
  const tabs = ['summary', 'postmortem', 'activity', 'runs', 'lineage'] as const
  const linkedEvidence = linkedIncidentEvidence(timeline, env)
  async function run(action: () => Promise<unknown>, clear?: () => void) {
    if (pending) return
    setPending(true)
    setError(undefined)
    try {
      await action()
      clear?.()
      await router.invalidate()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Action failed.')
    } finally {
      setPending(false)
    }
  }
  const update = (value: { owner?: string | null; comment?: string }) =>
    updateIncident({
      data: {
        env,
        id: incident.id,
        version: incident.version,
        idempotency_key: incidentOperationKey(
          env,
          incident.id,
          'update',
          `${incident.version}:${JSON.stringify(value)}`,
        ),
        update: value,
      },
    })
  const changeFollowUp = async (id: string, completed: boolean) => {
    const intent = `${id}:${completed}`
    await updateFollowUp({
      data: {
        env,
        id: incident.id,
        follow_up_id: id,
        completed,
        idempotency_key: incidentOperationKey(
          env,
          incident.id,
          'follow-up-update',
          intent,
        ),
      },
    })
    clearIncidentOperationKey(env, incident.id, 'follow-up-update', intent)
  }
  const addFollowUp = async (description: string, dueAt: string | null) => {
    const intent = `${description}:${dueAt}`
    await createFollowUp({
      data: {
        env,
        id: incident.id,
        description,
        due_at: dueAt,
        idempotency_key: incidentOperationKey(
          env,
          incident.id,
          'follow-up-create',
          intent,
        ),
      },
    })
    clearIncidentOperationKey(env, incident.id, 'follow-up-create', intent)
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:grid lg:grid-cols-[400px_minmax(0,1fr)] lg:overflow-hidden">
      <IncidentSummary
        env={env}
        incident={incident}
        timeline={timeline}
        followUps={followUps}
        linkedEvidence={linkedEvidence}
        owner={owner}
        comment={comment}
        pending={pending}
        error={error}
        onOwnerChange={setOwner}
        onCommentChange={setComment}
        onSaveOwner={() => run(() => update({ owner: owner.trim() || null }))}
        onAddComment={() =>
          run(
            () => update({ comment: comment.trim() }),
            () => setComment(''),
          )
        }
      />
      <main className="flex shrink-0 flex-col lg:min-h-0 lg:overflow-hidden">
        <div
          role="tablist"
          aria-label="Incident details"
          className="flex shrink-0 gap-1 overflow-x-auto border-b border-line px-4 pt-2 lg:px-7"
        >
          {tabs.map((value, index) => (
            <button
              key={value}
              type="button"
              ref={(node) => {
                tabRefs.current[index] = node
              }}
              role="tab"
              id={`incident-tab-${value}`}
              aria-controls="incident-tab-panel"
              aria-selected={tab === value}
              tabIndex={tab === value ? 0 : -1}
              onClick={() => setTab(value)}
              onKeyDown={(event) => {
                const next =
                  event.key === 'ArrowRight'
                    ? (index + 1) % tabs.length
                    : event.key === 'ArrowLeft'
                      ? (index - 1 + tabs.length) % tabs.length
                      : event.key === 'Home'
                        ? 0
                        : event.key === 'End'
                          ? tabs.length - 1
                          : -1
                if (next >= 0) {
                  event.preventDefault()
                  setTab(tabs[next])
                  tabRefs.current[next]?.focus()
                }
              }}
              className={
                tab === value
                  ? 'border-b-2 border-primary px-1.5 py-3 text-sm font-medium whitespace-nowrap text-foreground sm:px-3'
                  : 'px-1.5 py-3 text-sm whitespace-nowrap text-muted-foreground hover:text-foreground sm:px-3'
              }
            >
              {value === 'summary'
                ? 'Investigation'
                : value === 'postmortem'
                  ? 'Post-mortem'
                  : value[0].toUpperCase() + value.slice(1)}
            </button>
          ))}
        </div>
        <div
          id="incident-tab-panel"
          role="tabpanel"
          aria-labelledby={`incident-tab-${tab}`}
          tabIndex={0}
          className="flex min-h-0 flex-col gap-6 overflow-y-auto p-4 outline-none lg:px-7 lg:py-[22px]"
        >
          {incident.effects.length ? (
            <section
              aria-label="Delivery status"
              className="flex flex-col gap-2 rounded-lg border border-border-card p-3"
            >
              {incident.effects.map((effect) => (
                <p key={effect.id} className="m-0 text-sm">
                  {effect.kind === 'pause'
                    ? 'Downstream pause'
                    : 'Notification'}{' '}
                  · {effect.status} · {effect.attempts} attempts
                  {effect.error ? (
                    <span className="block text-bad-text">{effect.error}</span>
                  ) : null}
                </p>
              ))}
              {incident.effects.some((effect) => effect.status === 'failed') ? (
                <Button
                  variant="outline"
                  disabled={pending}
                  onClick={() =>
                    run(() =>
                      updateIncident({
                        data: {
                          env,
                          id: incident.id,
                          version: incident.version,
                          idempotency_key: incidentOperationKey(
                            env,
                            incident.id,
                            'retry-effects',
                            String(incident.version),
                          ),
                          update: { retry_effects: true },
                        },
                      }),
                    )
                  }
                >
                  Retry failed delivery
                </Button>
              ) : null}
            </section>
          ) : null}
          {tab === 'summary' ? (
            <>
              <IncidentInvestigation
                env={env}
                incident={incident}
                timeline={timeline}
                investigation={investigation}
              />
              <SchemaDecisions
                key={`${env}:${incident.id}`}
                env={env}
                incidentId={incident.id}
                kind={incident.kind}
              />
            </>
          ) : null}
          {tab === 'runs' ? <IncidentRuns env={env} runs={runs} /> : null}
          {tab === 'activity' ? (
            <section>
              <h2 className="mb-1 text-[15px] font-medium">Activity</h2>
              <p className="mt-0 mb-4 text-[13px] text-muted-foreground">
                Chronological incident audit trail
              </p>
              {timeline.length ? (
                <ol className="m-0 list-none border-l border-line p-0 pl-5">
                  {timeline.map((event) => (
                    <li key={event.id} className="relative pb-5 last:pb-0">
                      <span className="absolute top-1 -left-[24.5px] size-2 rounded-full bg-primary" />
                      <div className="flex flex-wrap gap-x-2 text-sm">
                        <strong>{event.kind.replaceAll('_', ' ')}</strong>
                        <span className="text-muted-foreground">
                          {event.actor} · {formatDate(event.occurred_at)}
                        </span>
                      </div>
                      {payloadText(event.payload) ? (
                        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-sunken p-3 text-xs text-text-2">
                          {payloadText(event.payload)}
                        </pre>
                      ) : null}
                    </li>
                  ))}
                </ol>
              ) : (
                <EmptyState title="No timeline events">
                  No persisted activity is available for this incident.
                </EmptyState>
              )}
            </section>
          ) : null}
          {tab === 'postmortem' ? (
            <section>
              <div className="mb-4">
                <h2 className="m-0 text-[15px] font-medium">
                  Post-mortem &amp; follow-ups
                </h2>
                <p className="m-0 mt-1 text-[13px] text-muted-foreground">
                  {timeline
                    .filter((event) => event.kind === 'resolution_comment')
                    .map((event) => payloadText(event.payload))
                    .join('\n') || 'No resolution narrative has been recorded.'}
                </p>
              </div>
              <div className="mb-3 flex items-center">
                <h2 className="m-0 text-[15px] font-medium">
                  Resolution &amp; follow-ups
                </h2>
                <span className="ml-auto text-xs text-muted-foreground">
                  {followUps.filter((item) => item.completed_at).length} of{' '}
                  {followUps.length} complete
                </span>
              </div>
              {followUps.length ? (
                <ul className="m-0 list-none rounded-xl border border-border-card p-0">
                  {followUps.map((item) => (
                    <li
                      key={item.id}
                      className="flex items-start gap-3 border-b border-line-soft p-3 last:border-0"
                    >
                      <Checkbox
                        checked={item.completed_at !== null}
                        disabled={pending}
                        aria-label={`Complete follow-up: ${item.description}`}
                        onCheckedChange={(checked) =>
                          run(() => changeFollowUp(item.id, checked === true))
                        }
                      />
                      <span className="min-w-0 flex-1 text-sm">
                        <span
                          className={
                            item.completed_at
                              ? 'text-muted-foreground line-through'
                              : ''
                          }
                        >
                          {item.description}
                        </span>
                        {item.due_at ? (
                          <span className="mt-1 block text-xs text-muted-foreground">
                            Due {formatDate(item.due_at)}
                          </span>
                        ) : null}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No follow-ups recorded.
                </p>
              )}
              <form
                className="mt-3 flex flex-col gap-2 sm:flex-row"
                onSubmit={(event) => {
                  event.preventDefault()
                  const description = followUp.trim()
                  const dueAt = due
                    ? new Date(`${due}T00:00:00`).toISOString()
                    : null
                  run(
                    () => addFollowUp(description, dueAt),
                    () => {
                      setFollowUp('')
                      setDue('')
                    },
                  )
                }}
              >
                <Input
                  aria-label="Follow-up description"
                  required
                  value={followUp}
                  onChange={(event) => setFollowUp(event.target.value)}
                  placeholder="Add a follow-up"
                />
                <Input
                  aria-label="Due date"
                  type="date"
                  value={due}
                  onChange={(event) => setDue(event.target.value)}
                  className="sm:w-44"
                />
                <Button type="submit" disabled={pending || !followUp.trim()}>
                  Add
                </Button>
              </form>
            </section>
          ) : null}
          {tab === 'lineage' ? (
            <section className="flex flex-col gap-3">
              <h2 className="m-0 text-[15px] font-medium">Lineage</h2>
              {linkedEvidence.length ? (
                <Card className="p-4">
                  <KeyValues keyWidth={90} items={linkedEvidence} />
                </Card>
              ) : (
                <EmptyState title="Lineage unavailable">
                  No branch or job lineage was persisted with this incident.
                </EmptyState>
              )}
            </section>
          ) : null}
        </div>
      </main>
    </div>
  )
}
