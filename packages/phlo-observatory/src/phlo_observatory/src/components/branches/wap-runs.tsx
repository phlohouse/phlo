/** Read-only, environment-scoped Write-Audit-Publish observations. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { RefreshCwIcon } from 'lucide-react'
import type { Env } from '@/lib/data/types'
import { getWapRuns } from '@/lib/data/api/branches'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Mono } from '@/components/phlo/status'

type WapPage = Awaited<ReturnType<typeof getWapRuns>>
type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; data: WapPage }
  | { status: 'error'; message: string }

const timestamps = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  timeZone: 'UTC',
})
const branchLabels = {
  present: 'Present in Nessie',
  absent: 'Not present in Nessie',
  unknown: 'Branch state not observed',
  not_applicable: 'Candidate namespace · no Nessie branch',
}

export function WapObservations({ data }: { data: WapPage }) {
  return (
    <>
      <p className="mt-0 mb-5 max-w-3xl text-sm text-muted-foreground">
        Temporary staging references for {data.env} runs. Branch-based WAP uses
        a Nessie branch; snapshot-based WAP uses a candidate namespace. Run
        success does not establish that data was published.
      </p>
      {!data.catalog_available ? (
        <p role="status" className="mb-4 text-sm text-warn-ink">
          The complete Nessie reference list is unavailable. Missing branches
          cannot be confirmed. Run observations remain visible.
        </p>
      ) : null}
      {data.scan_limited ? (
        <p role="status" className="mb-4 text-sm text-warn-ink">
          Showing matches within the {data.scan_limit} most recent Dagster runs.
          Older WAP runs and uncorrelated branches may not appear.
        </p>
      ) : null}
      {data.items.length === 0 ? (
        <div className="border-y border-line py-8">
          <h3 className="mt-0 mb-2 text-base font-medium">
            No WAP runs observed
          </h3>
          <p className="m-0 max-w-2xl text-sm text-muted-foreground">
            No matching launches were found in {data.scanned_runs} available
            Dagster runs. References without verified environment identity are
            not included. This does not prove that no temporary branches exist.
          </p>
        </div>
      ) : (
        <div role="table" aria-label={`${data.env} WAP observations`}>
          <div
            role="row"
            className="hidden grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1.5fr)] gap-4 border-b border-line py-2 text-xs text-muted-foreground md:grid"
          >
            {[
              'Staging reference',
              'Run status',
              'Reported lifecycle',
              'Job / run',
            ].map((label) => (
              <div role="columnheader" key={label}>
                {label}
              </div>
            ))}
          </div>
          {data.items.map((run) => (
            <div
              role="row"
              key={run.run_id}
              className="grid gap-3 border-b border-line py-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1.5fr)] md:gap-4"
            >
              <div role="cell" className="min-w-0">
                <Mono className="block break-all text-sm">
                  {run.staging_ref}
                </Mono>
                <div className="mt-1 text-xs text-muted-foreground">
                  {run.strategy === 'unknown'
                    ? 'Strategy not observed'
                    : `${run.strategy} WAP`}
                  {run.catalog_system ? ` · ${run.catalog_system}` : ''}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {branchLabels[run.branch_state]}
                </div>
                {run.branch_hash ? (
                  <Mono
                    className="mt-1 block text-xs text-muted-foreground"
                    title={run.branch_hash}
                  >
                    {run.branch_hash.slice(0, 12)}
                  </Mono>
                ) : null}
              </div>
              <div role="cell">
                <span className="mr-2 text-xs text-muted-foreground md:hidden">
                  Run
                </span>
                <Badge variant={run.status === 'FAILURE' ? 'bad' : 'outline'}>
                  {run.status.toLowerCase().replaceAll('_', ' ')}
                </Badge>
                <time
                  className="mt-1 block text-xs text-muted-foreground"
                  dateTime={run.created_at}
                  title={run.created_at}
                >
                  {timestamps.format(new Date(run.created_at))} UTC
                </time>
              </div>
              <div role="cell" className="text-sm">
                <span className="mr-2 text-xs text-muted-foreground md:hidden">
                  Reported lifecycle
                </span>
                {run.report_state === 'verified'
                  ? run.lifecycle_status?.replaceAll('_', ' ')
                  : run.report_state === 'invalid'
                    ? 'Report could not be verified'
                    : 'Report not available'}
                {run.reported_at ? (
                  <time
                    className="mt-1 block text-xs text-muted-foreground"
                    dateTime={run.reported_at}
                    title={run.reported_at}
                  >
                    {timestamps.format(new Date(run.reported_at))} UTC
                  </time>
                ) : null}
              </div>
              <div role="cell" className="min-w-0">
                <Link
                  to="/pipelines/$jobName"
                  params={{ jobName: run.job_id }}
                  search={{ env: data.env }}
                  className="block break-all text-sm text-primary underline-offset-4 hover:underline"
                >
                  {run.job_id}
                </Link>
                <Mono
                  className="mt-1 block break-all text-xs text-muted-foreground"
                  title={`Dagster run ${run.run_id}`}
                >
                  {run.run_id}
                </Mono>
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="mt-4 mb-0 max-w-3xl text-xs text-muted-foreground">
        Read-only observations matched by Dagster code location and WAP launch
        tags. Lifecycle reports require a matching digest-verified launch
        manifest. Promotion and cleanup stay with the WAP workflow.
      </p>
    </>
  )
}

export function WapRuns({ env }: { env: Env }) {
  const [state, setState] = React.useState<LoadState>({ status: 'loading' })
  const [revision, refresh] = React.useReducer((value: number) => value + 1, 0)
  React.useEffect(() => {
    let current = true
    setState({ status: 'loading' })
    getWapRuns({ data: { env } })
      .then((data) => {
        if (current) setState({ status: 'ready', data })
      })
      .catch((error: unknown) => {
        if (current)
          setState({
            status: 'error',
            message:
              error instanceof Error
                ? error.message
                : 'WAP observations could not be loaded.',
          })
      })
    return () => {
      current = false
    }
  }, [env, revision])
  return (
    <section aria-label="Write-Audit-Publish" className="px-4 py-5 lg:px-7">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="m-0 text-lg font-semibold">Write-Audit-Publish</h2>
        <Button
          variant="outline"
          disabled={state.status === 'loading'}
          onClick={refresh}
        >
          <RefreshCwIcon />
          {state.status === 'error' ? 'Retry' : 'Refresh'}
        </Button>
      </div>
      {state.status === 'loading' ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading WAP observations…
        </p>
      ) : state.status === 'error' ? (
        <p role="alert" className="text-sm text-bad-text">
          {state.message}
        </p>
      ) : (
        <WapObservations data={state.data} />
      )}
    </section>
  )
}
