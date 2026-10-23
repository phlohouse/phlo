/** Edits explicitly draft metric definitions against an observed source schema. */
import * as React from 'react'
import { PlusIcon, XIcon } from 'lucide-react'
import type {
  MetricDefinition,
  MetricModel,
  MetricSource,
} from '@/lib/data/api/metrics'
import { getMetricSource, saveMetricModel } from '@/lib/data/api/metrics'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'

export function metricColumnKind(
  value: string | null,
): MetricDefinition['dimensions'][number]['kind'] | null {
  const token = (value ?? '').toLowerCase().split('(')[0].trim()
  if (
    [
      'int',
      'integer',
      'bigint',
      'smallint',
      'tinyint',
      'double',
      'float',
      'real',
      'decimal',
      'numeric',
      'long',
    ].includes(token)
  )
    return 'number'
  if (
    [
      'date',
      'timestamp',
      'timestamptz',
      'timestamp with time zone',
      'timestamp without time zone',
    ].includes(token)
  )
    return 'time'
  if (['bool', 'boolean'].includes(token)) return 'boolean'
  if (['string', 'varchar', 'char', 'text'].includes(token)) return 'string'
  return null
}

function initialDefinition(model?: MetricModel): MetricDefinition {
  return model
    ? {
        model_id: model.model_id,
        description: model.description,
        owner: model.owner,
        asset_id: model.asset_id,
        dimensions: model.dimensions,
        measures: model.measures,
        time_dimension: model.time_dimension,
      }
    : {
        model_id: '',
        description: '',
        owner: '',
        asset_id: '',
        dimensions: [],
        measures: [],
        time_dimension: null,
      }
}

function sourceOptions(
  sources: Array<{ id: string; relation: string | null }>,
  selected: string,
) {
  const options = sources.map((item) => ({
    value: item.id,
    label: item.relation ?? item.id,
  }))
  if (selected && !sources.some((item) => item.id === selected))
    options.unshift({ value: selected, label: `Saved source: ${selected}` })
  return [{ value: '', label: 'Choose an observed asset' }, ...options]
}

export function MetricModelEditor({
  env,
  model,
  sources,
  onSaved,
  onCancel,
}: {
  env: string
  model?: MetricModel
  sources: Array<{ id: string; relation: string | null }>
  onSaved: (model: MetricModel) => void
  onCancel: () => void
}) {
  const [definition, setDefinition] = React.useState(() =>
    initialDefinition(model),
  )
  const [source, setSource] = React.useState<MetricSource>()
  const [sourceError, setSourceError] = React.useState<string>()
  const [error, setError] = React.useState<string>()
  const [busy, setBusy] = React.useState(false)
  React.useEffect(() => {
    if (!definition.asset_id) return
    let active = true
    setSource(undefined)
    setSourceError(undefined)
    getMetricSource({ data: { env, id: definition.asset_id } }).then(
      (next) => {
        if (active) setSource(next)
      },
      (caught) => {
        if (active)
          setSourceError(
            caught instanceof Error
              ? caught.message
              : 'Source schema unavailable.',
          )
      },
    )
    return () => {
      active = false
    }
  }, [env, definition.asset_id])
  const columns = (source?.columns ?? []).filter((column) =>
    metricColumnKind(column.type),
  )
  const patch = (value: Partial<MetricDefinition>) =>
    setDefinition((current) => ({ ...current, ...value }))
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(undefined)
    try {
      onSaved(
        await saveMetricModel({
          data: { env, definition, expected_version: model?.version ?? 0 },
        }),
      )
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'Could not save the draft model.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <form
      onSubmit={(event) => void save(event)}
      className="flex min-h-0 flex-1 flex-col overflow-auto"
    >
      <div className="flex items-center gap-3 border-b border-line px-5 py-3">
        <h2 className="m-0 text-sm font-medium">
          {model ? `Edit ${model.model_id}` : 'New metric model'}
        </h2>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          onClick={onCancel}
          aria-label="Close model editor"
        >
          <XIcon />
        </Button>
      </div>
      <div className="grid gap-5 p-5">
        <p className="m-0 max-w-[75ch] text-sm text-muted-foreground">
          Draft definitions are stored in this environment and ref. Saving does
          not publish a semantic provider, approve a model, or change Ask.
        </p>
        <SourceRepairNotice model={model} />
        <div className="grid gap-4 md:grid-cols-2">
          <label className="grid gap-1.5 text-sm">
            Model ID
            <Input
              required
              pattern="[a-z][a-z0-9_-]{0,62}"
              disabled={Boolean(model)}
              value={definition.model_id}
              onChange={(event) => patch({ model_id: event.target.value })}
            />
          </label>
          <label className="grid gap-1.5 text-sm">
            Owner
            <Input
              required
              maxLength={160}
              value={definition.owner}
              onChange={(event) => patch({ owner: event.target.value })}
            />
          </label>
        </div>
        <label className="grid gap-1.5 text-sm">
          Description
          <textarea
            required
            maxLength={4000}
            rows={2}
            className="rounded-lg border border-input bg-card p-3 text-sm"
            value={definition.description}
            onChange={(event) => patch({ description: event.target.value })}
          />
        </label>
        <label className="grid gap-1.5 text-sm">
          Built on
          <Select
            aria-label="Source asset"
            value={definition.asset_id}
            onValueChange={(asset_id) =>
              patch({
                asset_id,
                measures: [],
                dimensions: [],
                time_dimension: null,
              })
            }
            options={sourceOptions(sources, definition.asset_id)}
          />
        </label>
        <p className="m-0 text-xs text-muted-foreground">
          Changing source clears column mappings so they can be verified against
          the replacement schema. For an asset outside the inventory page, enter
          its exact authorized ID below.
        </p>
        <label className="grid gap-1.5 text-sm">
          Source asset ID
          <Input
            key={definition.asset_id}
            defaultValue={definition.asset_id}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                event.currentTarget.blur()
              }
            }}
            onBlur={(event) => {
              const asset_id = event.target.value.trim()
              if (asset_id !== definition.asset_id)
                patch({
                  asset_id,
                  measures: [],
                  dimensions: [],
                  time_dimension: null,
                })
            }}
          />
        </label>
        {sourceError ? (
          <p role="alert" className="m-0 text-sm text-bad-text">
            {sourceError}
          </p>
        ) : null}
        {definition.asset_id && !source && !sourceError ? (
          <p role="status" className="m-0 text-sm text-muted-foreground">
            Loading source columns…
          </p>
        ) : null}
        <DefinitionRows
          definition={definition}
          columns={columns}
          patch={patch}
        />
        <label className="grid gap-1.5 text-sm">
          Default time dimension
          <Select
            aria-label="Default time dimension"
            value={definition.time_dimension ?? ''}
            onValueChange={(value) => patch({ time_dimension: value || null })}
            options={[
              { value: '', label: 'None' },
              ...definition.dimensions
                .filter((dimension) => dimension.kind === 'time')
                .map((dimension) => ({
                  value: dimension.name,
                  label: dimension.name || '(name required)',
                })),
            ]}
          />
        </label>
        {error ? (
          <p role="alert" className="m-0 text-sm text-bad-text">
            {error}
          </p>
        ) : null}
        <div className="flex gap-2">
          <Button
            operation={
              model ? 'v1_metric_model_update' : 'v1_metric_model_create'
            }
            type="submit"
            disabled={busy || !source || !definition.measures.length}
          >
            {busy ? 'Saving…' : 'Save draft model'}
          </Button>
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </form>
  )
}

function SourceRepairNotice({ model }: { model?: MetricModel }) {
  if (model?.source_status !== 'unavailable') return null
  return (
    <p role="status" className="text-sm text-warn-text">
      Source repair required: {model.source_reason} Choose an authorized
      replacement, then remap its columns. The saved definition is unchanged
      until you save.
    </p>
  )
}
function DefinitionRows({
  definition,
  columns,
  patch,
}: {
  definition: MetricDefinition
  columns: MetricSource['columns']
  patch: (value: Partial<MetricDefinition>) => void
}) {
  const options = columns.map((column) => ({
    value: column.name,
    label: `${column.name} · ${column.type}`,
  }))
  return (
    <>
      <div className="grid gap-3">
        <div className="flex items-center gap-3">
          <h3 className="m-0 text-sm font-medium">
            Measures · {definition.measures.length}
          </h3>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!columns.length || definition.measures.length >= 20}
            onClick={() =>
              patch({
                measures: [
                  ...definition.measures,
                  {
                    name: '',
                    column: columns[0].name,
                    aggregation: 'count',
                    description: '',
                  },
                ],
              })
            }
          >
            <PlusIcon />
            Add measure
          </Button>
        </div>
        {definition.measures.map((measure, index) => (
          <div
            key={index}
            className="grid grid-cols-2 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_110px_32px] [&>input]:col-span-2 sm:[&>input]:col-span-1"
          >
            <Input
              aria-label={`Measure ${index + 1} name`}
              required
              pattern="[A-Za-z_][A-Za-z0-9_]{0,127}"
              placeholder="measure_name"
              value={measure.name}
              onChange={(event) =>
                patch({
                  measures: definition.measures.map((item, position) =>
                    position === index
                      ? { ...item, name: event.target.value }
                      : item,
                  ),
                })
              }
            />
            <Select
              aria-label={`Measure ${index + 1} column`}
              value={measure.column}
              options={options}
              onValueChange={(column) =>
                patch({
                  measures: definition.measures.map((item, position) =>
                    position === index ? { ...item, column } : item,
                  ),
                })
              }
            />
            <Select
              aria-label={`Measure ${index + 1} aggregation`}
              value={measure.aggregation}
              options={(
                ['count', 'count_distinct', 'sum', 'avg', 'min', 'max'] as const
              ).map((value) => ({ value, label: value }))}
              onValueChange={(aggregation) =>
                patch({
                  measures: definition.measures.map((item, position) =>
                    position === index ? { ...item, aggregation } : item,
                  ),
                })
              }
            />
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove measure ${index + 1}`}
              onClick={() =>
                patch({
                  measures: definition.measures.filter(
                    (_, position) => position !== index,
                  ),
                })
              }
            >
              <XIcon />
            </Button>
          </div>
        ))}
      </div>
      <div className="grid gap-3">
        <div className="flex items-center gap-3">
          <h3 className="m-0 text-sm font-medium">
            Dimensions · {definition.dimensions.length}
          </h3>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!columns.length || definition.dimensions.length >= 20}
            onClick={() =>
              patch({
                dimensions: [
                  ...definition.dimensions,
                  {
                    name: columns[0].name,
                    column: columns[0].name,
                    kind: metricColumnKind(columns[0].type)!,
                    description: '',
                  },
                ],
              })
            }
          >
            <PlusIcon />
            Add dimension
          </Button>
        </div>
        {definition.dimensions.map((dimension, index) => (
          <div
            key={index}
            className="grid grid-cols-2 items-center gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_80px_32px] [&>input]:col-span-2 sm:[&>input]:col-span-1"
          >
            <Input
              aria-label={`Dimension ${index + 1} name`}
              required
              pattern="[A-Za-z_][A-Za-z0-9_]{0,127}"
              value={dimension.name}
              onChange={(event) =>
                patch({
                  dimensions: definition.dimensions.map((item, position) =>
                    position === index
                      ? { ...item, name: event.target.value }
                      : item,
                  ),
                  time_dimension:
                    definition.time_dimension === dimension.name
                      ? event.target.value
                      : definition.time_dimension,
                })
              }
            />
            <Select
              aria-label={`Dimension ${index + 1} column`}
              value={dimension.column}
              options={options}
              onValueChange={(column) =>
                patch({
                  dimensions: definition.dimensions.map((item, position) =>
                    position === index
                      ? {
                          ...item,
                          column,
                          kind: metricColumnKind(
                            columns.find(
                              (candidate) => candidate.name === column,
                            )!.type,
                          )!,
                        }
                      : item,
                  ),
                  time_dimension:
                    definition.time_dimension === dimension.name
                      ? null
                      : definition.time_dimension,
                })
              }
            />
            <span className="text-xs text-muted-foreground">
              {dimension.kind}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove dimension ${index + 1}`}
              onClick={() =>
                patch({
                  dimensions: definition.dimensions.filter(
                    (_, position) => position !== index,
                  ),
                  time_dimension:
                    definition.time_dimension === dimension.name
                      ? null
                      : definition.time_dimension,
                })
              }
            >
              <XIcon />
            </Button>
          </div>
        ))}
      </div>
    </>
  )
}
