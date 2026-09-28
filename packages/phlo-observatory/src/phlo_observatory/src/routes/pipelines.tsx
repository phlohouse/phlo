/** v1-backed Jobs pipeline screen. Legacy routes remain untouched. */
import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import {
  getV1PipelineSnapshot,
  launchV1Job,
  newV1PipelineIdempotencyKey,
  pauseV1Schedule,
  resumeV1Schedule,
} from '@/observatory/api/pipelinesV1'
import {
  environmentChangeEvent,
  selectedEnvironment,
} from '@/observatory/api/environment'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/pipelines')({ component: Pipelines })

type ActionIntent =
  | { kind: 'launch'; jobId: string; key: string }
  | {
      kind: 'schedule'
      action: 'pause' | 'resume'
      scheduleId: string
      key: string
    }

export function Pipelines() {
  const [environment, setEnvironment] = useState(selectedEnvironment)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(() =>
    typeof window === 'undefined'
      ? null
      : new URLSearchParams(window.location.search).get('pipelineId'),
  )
  useEffect(() => {
    const update = () => setEnvironment(selectedEnvironment())
    window.addEventListener(environmentChangeEvent(), update)
    return () => window.removeEventListener(environmentChangeEvent(), update)
  }, [])
  const result = useLiveResource(
    () =>
      environment
        ? getV1PipelineSnapshot({ data: { environment } }).then((response) => ({
            ...response,
            data: response.data === null ? null : [response.data],
          }))
        : Promise.resolve({ data: null, error: null }),
    120_000,
    `observatory:pipelines:${environment ?? 'unselected'}`,
  )
  const snapshot = result.data?.[0]
  const jobs = snapshot?.jobs.items.filter(
    (job) => selectedJobId === null || job.id === selectedJobId,
  )

  function selectJob(id: string | null) {
    setSelectedJobId(id)
    const url = new URL(window.location.href)
    if (id) url.searchParams.set('pipelineId', id)
    else url.searchParams.delete('pipelineId')
    const query = url.searchParams.toString()
    window.history.replaceState(
      null,
      '',
      query ? `${url.pathname}?${query}` : url.pathname,
    )
  }

  return (
    <ObservatoryPage
      kicker="Operations"
      title="Pipelines"
      description="Jobs, schedules, and recent run evidence from the selected v1 environment."
    >
      {!environment ? (
        <Unavailable detail="Select prod or staging before loading pipelines. No environment is assumed." />
      ) : result.isLoading ? (
        <Unavailable
          title="Loading pipelines"
          detail={`Reading v1 jobs, schedules, and runs from ${environment}.`}
        />
      ) : result.error ? (
        <Unavailable title="Pipelines unavailable" detail={result.error} />
      ) : snapshot ? (
        <div className="phlo-observatory-detail-list">
          {selectedJobId && (
            <button onClick={() => selectJob(null)} type="button">
              Show all jobs
            </button>
          )}
          {jobs?.length === 0 ? (
            <Unavailable
              title={selectedJobId ? 'Job not found' : 'No jobs available'}
              detail={
                selectedJobId
                  ? `The v1 API did not return job ${selectedJobId} in ${environment}.`
                  : `v1 reported no jobs in ${environment}.`
              }
            />
          ) : (
            jobs?.map((job) => {
              const schedules = snapshot.schedules.items.filter(
                (item) => item.job_id === job.id,
              )
              const runs = snapshot.runs.items.filter(
                (item) => item.job_id === job.id,
              )
              return (
                <JobCard
                  environment={environment}
                  job={job}
                  key={job.id}
                  runs={runs}
                  schedules={schedules}
                  onSelect={() => selectJob(job.id)}
                />
              )
            })
          )}
        </div>
      ) : (
        <Unavailable detail="Pipeline data is not available." />
      )}
    </ObservatoryPage>
  )
}

function JobCard({
  environment,
  job,
  onSelect,
  runs,
  schedules,
}: {
  environment: 'prod' | 'staging'
  onSelect: () => void
  job: {
    id: string
    repository_name: string
    description: string | null
    selected_assets: Array<Array<string>>
  }
  runs: Array<{ run_id: string; status: string; created_at: string }>
  schedules: Array<{ id: string; status: string }>
}) {
  const [message, setMessage] = useState<string | null>(null)
  const [intent, setIntent] = useState<ActionIntent | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const idempotencyKeys = useRef(new Map<string, string>())
  const idempotencyKey = (action: string) => {
    const existing = idempotencyKeys.current.get(action)
    if (existing) return existing
    const next = newV1PipelineIdempotencyKey()
    idempotencyKeys.current.set(action, next)
    return next
  }
  const perform = (
    actionKey: string,
    request: () => Promise<{
      data: { status: string } | null
      error: string | null
    }>,
  ) => {
    setSubmitting(true)
    void request()
      .then((result) => {
        if (result.error) {
          setMessage(result.error)
          return
        }
        const status = result.data?.status ?? 'unknown'
        setMessage(
          `Action result: ${status}. Refresh to load current evidence.`,
        )
        if (status === 'accepted') {
          idempotencyKeys.current.delete(actionKey)
          setIntent(null)
        }
      })
      .finally(() => setSubmitting(false))
  }
  return (
    <section className="phlo-observatory-mini-row">
      <div>
        <button onClick={onSelect} type="button">
          {job.id}
        </button>
        <small>{job.description ?? 'No job description reported.'}</small>
      </div>
      <small>
        Repository: {job.repository_name} · Assets: {job.selected_assets.length}{' '}
        · Recent runs: {runs.length}
      </small>
      {runs.length > 0 && (
        <small>
          Latest observed run: {runs[0].run_id} ({runs[0].status})
        </small>
      )}
      <div className="phlo-observatory-action-row">
        <button
          disabled={submitting}
          onClick={() => {
            const key = `launch:${job.id}`
            setIntent({
              kind: 'launch',
              jobId: job.id,
              key: idempotencyKey(key),
            })
          }}
          type="button"
        >
          Launch job
        </button>
        {schedules.length === 0 ? (
          <small>Scheduling unavailable: this job has no v1 schedule.</small>
        ) : (
          schedules.map((schedule) => (
            <button
              key={schedule.id}
              disabled={submitting}
              onClick={() => {
                const action =
                  schedule.status === 'RUNNING' ? 'pause' : 'resume'
                const key = `${action}:${schedule.id}`
                setIntent({
                  kind: 'schedule',
                  action,
                  scheduleId: schedule.id,
                  key: idempotencyKey(key),
                })
              }}
              type="button"
            >
              {schedule.status === 'RUNNING'
                ? `Pause ${schedule.id}`
                : `Resume ${schedule.id}`}
            </button>
          ))
        )}
      </div>
      {intent && (
        <div className="phlo-observatory-panel-note" role="alert">
          <span>
            Confirm {intent.kind === 'launch' ? 'launch' : intent.action} of{' '}
            {intent.kind === 'launch' ? intent.jobId : intent.scheduleId} in{' '}
            {environment}.
          </span>
          <button
            disabled={submitting}
            onClick={() => {
              if (intent.kind === 'launch') {
                perform(intent.key, () =>
                  launchV1Job({
                    data: {
                      environment,
                      id: intent.jobId,
                      idempotencyKey: intent.key,
                    },
                  }),
                )
              } else {
                const action =
                  intent.action === 'pause' ? pauseV1Schedule : resumeV1Schedule
                perform(intent.key, () =>
                  action({
                    data: {
                      environment,
                      id: intent.scheduleId,
                      idempotencyKey: intent.key,
                    },
                  }),
                )
              }
            }}
            type="button"
          >
            Confirm action
          </button>
          <button
            disabled={submitting}
            onClick={() => setIntent(null)}
            type="button"
          >
            Cancel
          </button>
        </div>
      )}
      {message && <small>{message}</small>}
    </section>
  )
}

function Unavailable({
  detail,
  title = 'Pipelines unavailable',
}: {
  detail: string
  title?: string
}) {
  return (
    <div className="phlo-observatory-operation-empty">
      <div>
        <span className="phlo-observatory-inspector-label">Pipelines</span>
        <h2>{title}</h2>
        <p>{detail}</p>
      </div>
    </div>
  )
}
