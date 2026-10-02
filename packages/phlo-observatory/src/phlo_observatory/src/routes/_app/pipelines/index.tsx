/** Defines the pipeline catalogue and recent-run summary route. */
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { z } from 'zod'
import { getPipelineList } from '@/lib/data/api/pipelines'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { ViewSwitch, runColor } from '@/components/pipelines/bits'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/pipelines/')({
  validateSearch: z.object({ q: z.string().default('') }),
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getPipelineList({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Pipelines · phlo' }] }),
  component: PipelinesPage,
})

function PipelinesPage() {
  const { jobs, runs, env } = Route.useLoaderData()
  const { q } = Route.useSearch()
  const navigate = Route.useNavigate()
  const router = useRouter()
  const visible = jobs.filter((job) =>
    job.id.toLowerCase().includes(q.trim().toLowerCase()),
  )
  return (
    <>
      <PageHeader
        title="Pipelines"
        meta={`${jobs.length} jobs · ${env}`}
        actions={
          <>
            <Button variant="outline" onClick={() => void router.invalidate()}>
              Refresh
            </Button>
            <ViewSwitch current="list" env={env} />
          </>
        }
      />
      <div className="shrink-0 border-b border-line px-4 py-3">
        <label className="flex max-w-md flex-col gap-1.5 text-[13.5px]">
          Find a job
          <Input
            type="search"
            value={q}
            onChange={(event) =>
              void navigate({
                search: (p) => ({ ...p, q: event.target.value }),
                replace: true,
              })
            }
            placeholder="Filter by job name"
          />
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="hidden min-w-[780px] md:block">
          <Table aria-label="Jobs">
            <TableHeader>
              <TableRow>
                {[
                  'Job',
                  'Repository',
                  'Selected assets',
                  'Recent runs',
                  'Last observed run',
                ].map((label) => (
                  <TableHead key={label}>{label}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((job) => {
                const recent = runs
                  .filter((run) => run.job_id === job.id)
                  .slice(0, 24)
                return (
                  <TableRow key={job.id}>
                    <TableCell>
                      <Link
                        to="/pipelines/$jobName"
                        params={{ jobName: job.id }}
                        search={{ env }}
                        className="font-mono text-[13px] text-foreground hover:text-link"
                      >
                        {job.id}
                      </Link>
                    </TableCell>
                    <TableCell>{job.repository_name}</TableCell>
                    <TableCell>{job.selected_assets.length}</TableCell>
                    <TableCell>
                      <div
                        className="flex gap-0.5"
                        aria-label={`Recent runs for ${job.id}`}
                      >
                        {recent.toReversed().map((run) => (
                          <Link
                            key={run.run_id}
                            to="/pipelines/$jobName"
                            params={{ jobName: job.id }}
                            search={{ env, run: run.run_id }}
                            aria-label={`${run.status} run ${run.run_id}`}
                            title={`${run.status} · ${run.created_at}`}
                            className={cn(
                              'h-[22px] w-2 rounded-[2px]',
                              runColor(run.status),
                            )}
                          />
                        ))}
                        {!recent.length ? (
                          <span className="text-muted-foreground">
                            Not observed
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {recent[0]?.created_at ?? 'Not observed'}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
        <div className="flex flex-col gap-3 p-4 md:hidden">
          {visible.map((job) => (
            <Card key={job.id} className="gap-2 p-3.5">
              <Link
                to="/pipelines/$jobName"
                params={{ jobName: job.id }}
                search={{ env }}
                className="break-all font-mono text-sm text-foreground"
              >
                {job.id}
              </Link>
              <Eyebrow>{job.repository_name}</Eyebrow>
              <span className="text-xs text-muted-foreground">
                {job.selected_assets.length} selected assets ·{' '}
                {runs.filter((run) => run.job_id === job.id).length} observed
                runs
              </span>
            </Card>
          ))}
        </div>
        {!visible.length ? (
          <div className="p-5">
            <EmptyState
              title={
                jobs.length ? 'No jobs match' : 'No jobs in this environment'
              }
            >
              Try another name. No runs does not imply a healthy pipeline.
            </EmptyState>
          </div>
        ) : null}
      </div>
      <div className="shrink-0 border-t border-line px-4 py-3 text-[13px] text-muted-foreground">
        Showing {visible.length} of {jobs.length} jobs. Run strips use up to 24
        runs from the latest 100 environment-scoped run records, not a complete
        history.
      </div>
    </>
  )
}
