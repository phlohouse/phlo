/** Persisted draft metric definitions and real SQL previews over observed assets. */
import * as React from 'react'
import { createFileRoute, useRouter } from '@tanstack/react-router'
import { PlusIcon, SearchIcon } from 'lucide-react'
import { z } from 'zod'
import type { MetricModel } from '@/lib/data/api/metrics'
import { getMetricModel, getMetricsWorkspace } from '@/lib/data/api/metrics'
import { DraftLifecycleActions } from '@/components/phlo/draft-lifecycle'
import { MetricModelEditor } from '@/components/metrics/model-editor'
import { MetricModelPreview } from '@/components/metrics/model-preview'
import { PageHeader } from '@/components/phlo/page'
import { PageSkeleton, RouteError } from '@/components/phlo/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/metrics')({
  validateSearch: z.object({
    model: z.string().optional(),
    archived: z.boolean().default(false),
  }),
  loaderDeps: ({ search }) => ({
    env: search.env,
    model: search.model,
    include_archived: search.archived,
  }),
  loader: async ({ deps }) => ({
    ...(await getMetricsWorkspace({ data: deps })),
    selected_model: deps.model
      ? await getMetricModel({ data: { env: deps.env, id: deps.model } })
      : null,
  }),
  head: () => ({ meta: [{ title: 'Metrics · phlo' }] }),
  pendingComponent: PageSkeleton,
  errorComponent: RouteError,
  component: MetricsPage,
})

function MetricsPage() {
  const data = Route.useLoaderData()
  const { model: selected, archived } = Route.useSearch()
  const router = useRouter()
  const navigate = Route.useNavigate()
  const [models, setModels] = React.useState(data.items)
  const [editor, setEditor] = React.useState<MetricModel | 'new' | null>(null)
  const [query, setQuery] = React.useState('')
  React.useEffect(() => {
    setModels(data.items)
    setEditor(null)
  }, [data])
  const model =
    models.find((item) => item.model_id === selected) ?? data.selected_model
  const select = (id: string | undefined) =>
    void navigate({ search: (previous) => ({ ...previous, model: id }) })
  const saved = (next: MetricModel) => {
    setModels((items) => [
      ...items.filter((item) => item.model_id !== next.model_id),
      next,
    ])
    setEditor(null)
    select(next.model_id)
  }
  const count = models.reduce((total, item) => total + item.measures.length, 0)
  return (
    <>
      <PageHeader
        title={model ? model.model_id : 'Metrics'}
        crumbs={model ? [{ label: 'Metrics', to: '/metrics' }] : undefined}
        meta={
          model
            ? `${model.status} v${model.version} · ${model.nessie_ref} · ${model.owner}`
            : 'Business definitions over observed lakehouse assets'
        }
        actions={
          <>
            {model && model.status === 'draft' ? (
              <Button
                operation="v1_metric_model_update"
                variant="outline"
                onClick={() => setEditor(model)}
              >
                Edit draft
              </Button>
            ) : null}
            {model ? (
              <DraftLifecycleActions
                key={`${model.model_id}:${model.version}`}
                kind="metric"
                env={data.env}
                id={model.model_id}
                version={model.version}
                status={model.status}
                onChanged={() => void router.invalidate()}
              />
            ) : null}
            <Button
              operation="v1_metric_model_create"
              onClick={() => setEditor('new')}
            >
              <PlusIcon />
              New model
            </Button>
          </>
        }
      />
      {editor ? (
        <MetricModelEditor
          key={`${data.env}:${editor === 'new' ? 'new' : editor.model_id}`}
          env={data.env}
          model={editor === 'new' ? undefined : editor}
          sources={data.sources}
          onSaved={saved}
          onCancel={() => setEditor(null)}
        />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row lg:overflow-hidden">
          <aside
            aria-label="Metric models"
            className="w-full shrink-0 border-b border-line p-3 lg:w-[280px] lg:overflow-y-auto lg:border-r lg:border-b-0"
          >
            <label className="mb-3 flex items-center gap-2 px-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={archived}
                onChange={(event) =>
                  void navigate({
                    search: (previous) => ({
                      ...previous,
                      archived: event.target.checked,
                    }),
                  })
                }
              />
              Include archived definitions
            </label>
            <div className="mb-2 flex items-center justify-between px-2 text-xs text-muted-foreground">
              <span>Models · {models.length}</span>
              <button
                type="button"
                className="text-link"
                onClick={() => select(undefined)}
              >
                All metrics · {count}
              </button>
            </div>
            {models.map((item) => (
              <button
                type="button"
                key={item.model_id}
                aria-pressed={item.model_id === selected}
                onClick={() => select(item.model_id)}
                className={cn(
                  'mb-1 grid w-full gap-1.5 rounded-lg p-2.5 text-left outline-none hover:bg-raised focus-visible:ring-2 focus-visible:ring-ring',
                  item.model_id === selected && 'bg-soft',
                )}
              >
                <span className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px]">
                    {item.model_id}
                  </span>
                  <Badge variant="neutral">
                    {item.status === 'archived' ? 'Archived' : 'Draft'}
                  </Badge>
                </span>
                <span className="line-clamp-2 text-xs text-muted-foreground">
                  {item.description}
                </span>
                <span className="truncate font-mono text-[11px] text-muted-foreground">
                  {item.relation} · {item.measures.length} measures
                </span>
              </button>
            ))}
            {!models.length ? (
              <p className="px-2 text-sm text-muted-foreground">
                No draft models yet. Create a model from an observed asset.
              </p>
            ) : null}
            <p className="m-2 mt-5 border-t border-line pt-3 text-xs text-muted-foreground">
              Stored draft definitions. No semantic provider is published by
              this editor.
            </p>
            {data.sources_truncated ? (
              <p className="m-2 text-xs text-muted-foreground">
                Source picker shows the first100 assets.
              </p>
            ) : null}
          </aside>
          {model ? (
            <MetricDefinitionView
              key={`${data.env}:${model.model_id}:${model.version}`}
              model={model}
              env={data.env}
            />
          ) : (
            <section
              aria-label="All metrics"
              className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto"
            >
              <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
                <span className="text-sm">All metrics · {count}</span>
                <label className="ml-auto flex h-9 items-center gap-2 rounded-lg border border-input bg-card px-3">
                  <SearchIcon className="size-3.5 text-muted-foreground" />
                  <input
                    aria-label="Find a metric"
                    type="search"
                    placeholder="Find a metric or model"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    className="min-w-0 bg-transparent text-sm outline-none"
                  />
                </label>
              </div>
              <div className="p-5">
                <p className="mb-4 mt-0 text-xs text-muted-foreground">
                  Draft definitions only. Verification, Ask usage and publishing
                  are not available.
                </p>
                <MetricTable models={models} query={query} select={select} />
              </div>
            </section>
          )}
        </div>
      )}
    </>
  )
}

function MetricTable({
  models,
  query,
  select,
}: {
  models: Array<MetricModel>
  query: string
  select: (id: string) => void
}) {
  const rows = models
    .flatMap((model) => model.measures.map((measure) => ({ model, measure })))
    .filter(({ model, measure }) =>
      `${model.model_id} ${measure.name} ${measure.description}`
        .toLowerCase()
        .includes(query.toLowerCase()),
    )
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table
        className="w-full border-collapse text-left text-[13px]"
        aria-label="Metric definitions"
      >
        <thead className="border-b border-line bg-raised text-xs text-muted-foreground">
          <tr>
            {['Metric', 'Calculated as', 'Model', 'Owner'].map((label) => (
              <th scope="col" key={label} className="px-3 py-3 font-normal">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ model, measure }) => (
            <tr
              key={`${model.model_id}:${measure.name}`}
              className="border-b border-line-soft last:border-b-0"
            >
              <td className="px-3 py-4 font-mono text-xs">
                <button
                  type="button"
                  onClick={() => select(model.model_id)}
                  className="text-left text-link"
                >
                  {measure.name}
                </button>
              </td>
              <td className="px-3 py-4 font-mono text-xs text-muted-foreground">
                {measure.aggregation}({measure.column})
              </td>
              <td className="px-3 py-4 font-mono text-xs">{model.model_id}</td>
              <td className="px-3 py-4 text-muted-foreground">{model.owner}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length ? (
        <p className="p-4 text-sm text-muted-foreground">
          No metrics match this search.
        </p>
      ) : null}
    </div>
  )
}

function MetricDefinitionView({
  model,
  env,
}: {
  model: MetricModel
  env: string
}) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto xl:flex-row">
      <section aria-label="Model definition" className="min-w-0 flex-1 p-5">
        <p className="mb-5 mt-0 text-sm leading-relaxed text-text-2">
          {model.description}
        </p>
        <div className="mb-6 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>Built on</span>
          <span className="rounded-lg border border-line bg-raised px-3 py-2 font-mono text-text-2">
            {model.relation}
          </span>
          <Badge variant="neutral">
            {model.status} v{model.version}
          </Badge>
        </div>
        <h2 className="mb-3 text-sm font-medium">
          Measures · {model.measures.length}
        </h2>
        <MetricTable models={[model]} query="" select={() => undefined} />
        <h2 className="mb-3 mt-6 text-sm font-medium">
          Dimensions · {model.dimensions.length}
        </h2>
        <dl className="m-0 grid divide-y divide-line-soft rounded-lg border border-line">
          {model.dimensions.map((dimension) => (
            <div
              key={dimension.name}
              className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_70px] gap-3 px-3 py-3 text-xs"
            >
              <dt className="font-mono">{dimension.name}</dt>
              <dd className="m-0 font-mono text-muted-foreground">
                {dimension.column}
              </dd>
              <dd className="m-0 text-muted-foreground">{dimension.kind}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-xs text-muted-foreground">
          Default time dimension: {model.time_dimension ?? 'None'}. Updated{' '}
          {new Date(model.updated_at).toLocaleString()}.
        </p>
        <details className="mt-5">
          <summary className="cursor-pointer text-xs text-link">
            Definition JSON
          </summary>
          <pre className="overflow-auto rounded-lg bg-background p-4 font-mono text-xs">
            {JSON.stringify(model, null, 2)}
          </pre>
        </details>
      </section>
      {model.status === 'archived' ? (
        <aside className="p-5 text-sm text-muted-foreground">
          Archived definition retained for review and export. Restore it before
          editing or running a preview. No historical revision snapshots or
          publication are implied.
        </aside>
      ) : (
        <MetricModelPreview model={model} env={env} />
      )}
    </div>
  )
}
