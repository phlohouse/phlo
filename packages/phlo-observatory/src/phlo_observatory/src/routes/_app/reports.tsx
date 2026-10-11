import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'
import { getRunReport } from '@/lib/data/api/reports'
import { PageHeader } from '@/components/phlo/page'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

export const Route = createFileRoute('/_app/reports')({
  validateSearch: z.object({
    project: z.string().min(1).optional(),
    run: z.string().min(1).optional(),
    attempt: z.number().int().positive().default(1),
  }),
  loaderDeps: ({ search }) => ({
    project: search.project,
    run: search.run,
    attempt: search.attempt,
  }),
  loader: async ({ deps }) => {
    if (!deps.project || !deps.run) return { kind: 'empty' as const }
    try {
      return {
        kind: 'report' as const,
        report: await getRunReport({
          data: { project: deps.project, run: deps.run, attempt: deps.attempt },
        }),
      }
    } catch (error) {
      return {
        kind: 'unavailable' as const,
        message:
          error instanceof Error ? error.message : 'Run evidence unavailable.',
      }
    }
  },
  head: () => ({ meta: [{ title: 'Durable run reports · phlo' }] }),
  component: ReportsPage,
})

function ReportsPage() {
  const data = Route.useLoaderData()
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <>
      <PageHeader
        title="Durable run reports"
        crumbs={[{ label: 'Jobs', to: '/pipelines' }]}
      />
      <div className="min-h-0 overflow-y-auto p-4 lg:p-7">
        <p className="text-sm text-muted-foreground">
          One durable project, logical run and attempt. The environment selector
          does not remap this producer-recorded identity. A Dagster run ID is
          not assumed to be a logical run ID.
        </p>
        <form
          className="mb-6 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            const form = new FormData(event.currentTarget)
            void navigate({
              search: (previous) => ({
                ...previous,
                project: String(form.get('project')),
                run: String(form.get('run')),
                attempt: Number(form.get('attempt')),
              }),
            })
          }}
        >
          <label className="text-sm">
            Project ID
            <Input name="project" defaultValue={search.project} required />
          </label>
          <label className="text-sm">
            Logical run ID
            <Input name="run" defaultValue={search.run} required />
          </label>
          <label className="text-sm">
            Attempt
            <Input
              name="attempt"
              type="number"
              min={1}
              step={1}
              defaultValue={search.attempt}
              required
            />
          </label>
          <Button type="submit">Load report</Button>
        </form>
        {data.kind === 'empty' ? (
          <p>
            Enter the exact durable run identity to inspect retained evidence.
          </p>
        ) : null}
        {data.kind === 'unavailable' ? (
          <p role="alert">
            Report unavailable. {data.message} No partial report or success
            outcome is inferred.
          </p>
        ) : null}
        {data.kind === 'report' ? (
          <>
            <h2 className="break-all text-lg font-medium">
              {data.report.project_id} / {data.report.run_id} / attempt{' '}
              {data.report.attempt}
            </h2>
            <p>
              Terminal outcome:{' '}
              {data.report.terminal_outcome?.status ??
                'unavailable or ambiguous'}
              . Evidence:{' '}
              {data.report.lifecycle.run?.evidence_completeness ??
                'run header unavailable'}
              .
            </p>
            {data.report.gaps.length ? (
              <section aria-label="Evidence gaps">
                <h3 className="text-sm font-medium">Evidence gaps</h3>
                <ul>
                  {data.report.gaps.map((gap, index) => (
                    <li key={index}>
                      {gap.field} · {gap.status}: {gap.reason}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {Object.entries(data.report)
              .filter(
                ([key]) =>
                  ![
                    'project_id',
                    'run_id',
                    'attempt',
                    'schema_version',
                    'gaps',
                  ].includes(key),
              )
              .map(([key, value]) => (
                <details
                  key={key}
                  className="my-3 rounded border border-line p-3"
                >
                  <summary className="cursor-pointer font-medium">
                    {key.replaceAll('_', ' ')}
                  </summary>
                  <pre className="overflow-x-auto text-xs">
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </details>
              ))}
          </>
        ) : null}
      </div>
    </>
  )
}
