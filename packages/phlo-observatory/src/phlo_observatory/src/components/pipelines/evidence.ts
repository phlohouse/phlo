/** Finds observed overlap, without claiming a shared root cause. */
import type { ApiRun, getPipelineList } from '@/lib/data/api/pipelines'
import type { z } from 'zod'
import type { pipelineSearchSchema } from './bits'

type PipelineData = Awaited<ReturnType<typeof getPipelineList>>
type Job = PipelineData['jobs'][number]
type Search = z.infer<typeof pipelineSearchSchema>
type Identity = { subject: string; email: string | null }

export function observedRunState(latest?: ApiRun) {
  if (!latest) return 'unknown'
  if (latest.status === 'FAILURE') return 'failed'
  if (latest.status === 'SUCCESS') return 'succeeded'
  return 'other'
}

export function ownsJob(job: Job, identity: Identity) {
  return job.owners.some(
    (owner) => owner === identity.email || owner === identity.subject,
  )
}

export function pipelineMatches(
  job: Job,
  latest: ApiRun | undefined,
  search: Search,
  identity: Identity,
) {
  return (
    job.id.toLowerCase().includes(search.q.trim().toLowerCase()) &&
    search.states.includes(observedRunState(latest)) &&
    (!search.sources.length || search.sources.includes(job.source ?? '')) &&
    (!search.owners.length ||
      job.owners.some((owner) => search.owners.includes(owner)) ||
      (!job.owners.length && search.owners.includes(''))) &&
    (!search.saved.includes('mine') || ownsJob(job, identity)) &&
    (!search.saved.includes('release') || job.feeds_batch_release)
  )
}

export function pipelineGroupName(job: Job, by: Search['by']) {
  return by === 'domain'
    ? (job.domain ?? 'Unknown domain')
    : by === 'owner'
      ? job.owners.join(' · ') || 'Unknown owner'
      : (job.source ?? 'Unknown source')
}

export function overlappingMaintenance(
  windows: PipelineData['maintenance']['items'],
  start: number,
  end: number,
) {
  return windows.filter(
    (window) =>
      Date.parse(window.starts_at) < end && Date.parse(window.ends_at) > start,
  )
}

export function correlatedFailures(
  jobs: PipelineData['jobs'],
  runs: Array<ApiRun>,
) {
  const failed = runs
    .filter((run) => run.status === 'FAILURE')
    .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  const groups: Array<{ source: string; runs: Array<ApiRun> }> = []
  for (const run of failed) {
    const source = jobs.find((job) => job.id === run.job_id)?.source
    if (!source) continue
    const group = groups.findLast(
      (item) =>
        item.source === source &&
        Date.parse(run.created_at) - Date.parse(item.runs[0].created_at) <=
          10 * 60 * 1000,
    )
    if (group) group.runs.push(run)
    else groups.push({ source, runs: [run] })
  }
  return groups.filter(
    (group) => new Set(group.runs.map((run) => run.job_id)).size > 1,
  )
}
