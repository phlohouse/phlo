/** Shows the Phlo Transform workspace declared by the active transform provider. */
import { Link, createFileRoute } from '@tanstack/react-router'
import { SparklesIcon } from 'lucide-react'
import type { TransformModel } from '@/lib/data/api/transform'
import { getTransformWorkspace } from '@/lib/data/api/transform'
import { Eyebrow, PageBody, PageHeader } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Mono } from '@/components/phlo/status'
import { Stat } from '@/components/phlo/kpi'
import { Badge } from '@/components/ui/badge'

export const Route = createFileRoute('/_app/transform')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getTransformWorkspace({ data: deps.env }),
  head: () => ({ meta: [{ title: 'Transform · phlo' }] }),
  component: TransformPage,
})

const runTone: Record<string, 'ok' | 'bad' | 'neutral'> = {
  passed: 'ok',
  success: 'ok',
  failed: 'bad',
  failure: 'bad',
  skipped: 'neutral',
}

function runBadge(status: string | null | undefined) {
  if (!status)
    return <span className="text-muted-foreground">Not observed</span>
  return <Badge variant={runTone[status] ?? 'neutral'}>{status}</Badge>
}

function ModelRow({ model, env }: { model: TransformModel; env: string }) {
  const tests = model.tests?.length ?? 0
  const lastRun = model.last_run
  return (
    <tr className="border-b border-line-soft last:border-0">
      <td className="px-3 py-2.5 align-top break-words">
        <Link
          to="/assets/$assetId"
          params={{ assetId: model.asset_id }}
          search={{ env }}
          className="font-mono text-[12.5px] underline-offset-2 hover:underline"
        >
          {model.asset_id}
        </Link>
      </td>
      <td className="px-3 py-2.5 align-top break-words">
        <Mono>{model.model}</Mono>
      </td>
      <td className="px-3 py-2.5 align-top whitespace-nowrap text-muted-foreground">
        {model.materialization ?? '—'}
      </td>
      <td className="px-3 py-2.5 align-top break-words text-muted-foreground">
        <Mono>{model.target ?? '—'}</Mono>
      </td>
      <td className="px-3 py-2.5 align-top font-mono text-xs whitespace-nowrap text-muted-foreground">
        {model.version ?? '—'}
      </td>
      <td className="px-3 py-2.5 align-top font-mono text-xs text-muted-foreground">
        {tests}
      </td>
      <td className="px-3 py-2.5 align-top">
        <span className="flex flex-col items-start gap-0.5">
          {runBadge(lastRun?.status)}
          {lastRun?.duration_ms != null ? (
            <span className="text-[11px] text-muted-foreground">
              {lastRun.duration_ms} ms
            </span>
          ) : null}
        </span>
      </td>
    </tr>
  )
}

function TransformPage() {
  const data = Route.useLoaderData()

  if (data.status === 'unavailable') {
    return (
      <>
        <PageHeader
          title={
            <span className="flex items-center gap-2">
              <SparklesIcon className="size-4 text-muted-foreground" />{' '}
              Transform
            </span>
          }
          meta="Transformation workspace"
        />
        <PageBody className="gap-6 lg:p-8">
          <EmptyState
            title="No transformation assets in this environment"
            icon={<SparklesIcon className="size-3" />}
            action={
              <code className="rounded-md bg-soft px-2.5 py-1.5 font-mono text-xs">
                phlo transform status
              </code>
            }
          >
            {data.reason ??
              'Install a dbt project or phlo-transform workspace, select a provider, and restart Dagster.'}
          </EmptyState>
        </PageBody>
      </>
    )
  }

  const tests = data.models.flatMap((model) =>
    (model.tests ?? []).map((test) => ({ test, model })),
  )
  const sources = [
    ...new Set(data.models.flatMap((model) => model.sources ?? [])),
  ].sort()

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <SparklesIcon className="size-4 text-muted-foreground" /> Transform
          </span>
        }
        meta="Transformation workspace declared by the active provider"
        actions={
          <Link
            to="/assets"
            search={{ env: data.env }}
            className="text-[13px] underline-offset-2 hover:underline"
          >
            Browse assets
          </Link>
        }
      />
      <PageBody className="gap-6 lg:p-8">
        <section
          aria-label="Workspace status"
          className="grid grid-cols-2 gap-3 lg:grid-cols-4"
        >
          <Stat
            label="Engine"
            value={data.engine}
            sub="Active transformation provider"
          />
          <Stat label="Models" value={data.model_count} sub="Compiled assets" />
          <Stat
            label="Tests"
            value={data.test_count}
            sub="Blocking asset checks"
          />
          <Stat
            label="Sources"
            value={data.source_count}
            sub="Physical relations read"
          />
        </section>

        <p className="m-0 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>Workspace</span>
          <Mono className="text-foreground">
            {data.workspace ?? 'not declared'}
          </Mono>
        </p>

        <section
          aria-label="Transform models"
          className="flex flex-col gap-2.5"
        >
          <div className="flex items-baseline gap-2">
            <Eyebrow>Models</Eyebrow>
            <span className="text-xs text-muted-foreground">
              {data.model_count} compiled{' '}
              {data.model_count === 1 ? 'model' : 'models'} · versions are
              content hashes
            </span>
          </div>
          <div className="overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[860px] table-fixed border-collapse text-left text-[12.5px]">
              <colgroup>
                <col className="w-[180px]" />
                <col className="w-[220px]" />
                <col className="w-[100px]" />
                <col className="w-[220px]" />
                <col className="w-[75px]" />
                <col className="w-[55px]" />
                <col className="w-[100px]" />
              </colgroup>
              <thead className="text-xs text-muted-foreground">
                <tr className="border-b border-line">
                  <th className="px-3 py-2 font-normal">Asset</th>
                  <th className="px-3 py-2 font-normal">Model</th>
                  <th className="px-3 py-2 font-normal">Materialization</th>
                  <th className="px-3 py-2 font-normal">Target</th>
                  <th className="px-3 py-2 font-normal">Version</th>
                  <th className="px-3 py-2 font-normal">Tests</th>
                  <th className="px-3 py-2 font-normal">Last run</th>
                </tr>
              </thead>
              <tbody>
                {data.models.map((model) => (
                  <ModelRow key={model.asset_id} model={model} env={data.env} />
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
          <section
            aria-label="Transform tests"
            className="flex flex-col gap-2.5"
          >
            <div className="flex items-baseline gap-2">
              <Eyebrow>Tests</Eyebrow>
              <span className="text-xs text-muted-foreground">
                {data.test_count} blocking asset checks
              </span>
            </div>
            <div className="overflow-x-auto rounded-lg border border-line">
              <table className="w-full min-w-[640px] table-fixed border-collapse text-left text-[12.5px]">
                <colgroup>
                  <col />
                  <col className="w-[220px]" />
                </colgroup>
                <thead className="text-xs text-muted-foreground">
                  <tr className="border-b border-line">
                    <th className="px-3 py-2 font-normal">Test</th>
                    <th className="px-3 py-2 font-normal">Asset</th>
                  </tr>
                </thead>
                <tbody>
                  {tests.map(({ test, model }) => (
                    <tr
                      key={`${model.asset_id}:${test}`}
                      className="border-b border-line-soft last:border-0"
                    >
                      <td className="px-3 py-2 align-top break-words">
                        <Mono>{test}</Mono>
                      </td>
                      <td className="px-3 py-2 align-top whitespace-nowrap">
                        <Link
                          to="/assets/$assetId"
                          params={{ assetId: model.asset_id }}
                          search={{ env: data.env }}
                          className="font-mono text-[12.5px] underline-offset-2 hover:underline"
                        >
                          {model.asset_id}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section
            aria-label="Transform sources"
            className="flex flex-col gap-2.5"
          >
            <Eyebrow>Sources</Eyebrow>
            <div className="flex flex-col gap-1.5 rounded-lg border border-line px-3 py-2.5">
              {sources.length ? (
                sources.map((source) => (
                  <Mono key={source} className="text-[12.5px]">
                    {source}
                  </Mono>
                ))
              ) : (
                <span className="text-[13px] text-muted-foreground">
                  No external sources are declared.
                </span>
              )}
            </div>
            <p className="m-0 text-xs text-muted-foreground">
              Source bindings come from the provider; ingestion edges appear as
              asset dependencies.
            </p>
          </section>
        </div>
      </PageBody>
    </>
  )
}
