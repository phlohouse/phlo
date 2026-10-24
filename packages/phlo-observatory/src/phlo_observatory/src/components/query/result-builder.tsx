/** Builds charts and pivot tables from the actual returned query rows. */
import * as React from 'react'
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Legend,
  Line,
  ReferenceArea,
  ReferenceLine,
  Scatter,
  XAxis,
  YAxis,
} from 'recharts'
import type { QueryResult } from '@/lib/data/api/query'
import type { QueryView } from '@/lib/data/api/query-view'
import type { ResultAggregation } from '@/lib/query-results'
import { restoreQueryView } from '@/lib/data/api/query-view'
import {
  aggregateQueryResult,
  numericResultColumns,
  scatterResultData,
} from '@/lib/query-results'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { Select } from '@/components/ui/select'
import { Segmented } from '@/components/ui/toggle-group'

const colors = ['var(--primary)', '#57c5d5', '#aeb4bf', '#dbab50', '#b298e7']
const operations = [
  { value: 'avg', label: 'Avg' },
  { value: 'sum', label: 'Sum' },
  { value: 'min', label: 'Min' },
  { value: 'max', label: 'Max' },
  { value: 'count', label: 'Count values' },
] as const

type Builder = ReturnType<typeof useResultBuilder>
type BuilderProps = {
  result: QueryResult
  config?: QueryView
  onConfigChange?: (config: QueryView) => void
}
function useConfiguration(
  config?: QueryView,
  onChange?: (config: QueryView) => void,
) {
  const [local, setLocal] = React.useState(() => restoreQueryView())
  const value = config ?? local
  const update = (patch: Partial<QueryView>) => {
    const next = { ...value, ...patch }
    setLocal(next)
    onChange?.(next)
  }
  return { config: value, update }
}
function useResultBuilder(
  result: QueryResult,
  config: QueryView,
  update: (patch: Partial<QueryView>) => void,
) {
  const numeric = numericResultColumns(result)
  const dimensions = result.columns.filter(
    (column) => !numeric.includes(column),
  )
  const x =
    config.x === '' || result.columns.some((column) => column.name === config.x)
      ? (config.x ?? '')
      : (dimensions[0]?.name ?? '')
  const group = result.columns.some((column) => column.name === config.group)
    ? config.group
    : ''
  const value =
    numeric.find((column) => column.name === config.y)?.name ??
    numeric[0]?.name ??
    ''
  const operation = config.operation
  const data = React.useMemo(
    () =>
      aggregateQueryResult(result, x, value, group, operation, config.bucket),
    [result, x, value, group, operation, config.bucket],
  )
  return {
    numeric,
    bucket: config.bucket,
    setBucket: (bucket: QueryView['bucket']) => update({ bucket }),
    temporal: /date|timestamp/i.test(
      result.columns.find((column) => column.name === x)?.type ?? '',
    ),
    x,
    setX: (nextX: string) => update({ x: nextX }),
    y: value,
    setY: (y: string) => update({ y }),
    group,
    setGroup: (nextGroup: string) => update({ group: nextGroup }),
    operation,
    setOperation: (nextOperation: ResultAggregation) =>
      update({ operation: nextOperation }),
    data,
  }
}

function BuilderFields({
  result,
  builder,
  pivot = false,
}: {
  result: QueryResult
  builder: Builder
  pivot?: boolean
}) {
  const fieldOptions = result.columns.map((column) => ({
    value: column.name,
    label: column.name,
  }))
  return (
    <>
      <label className="grid gap-1.5 text-[13px]">
        {pivot ? 'Rows' : 'X axis'}
        <Select
          aria-label={pivot ? 'Pivot rows' : 'X axis'}
          value={builder.x}
          onValueChange={builder.setX}
          options={[{ value: '', label: 'Row number' }, ...fieldOptions]}
        />
      </label>
      {builder.temporal ? (
        <label className="grid gap-1.5 text-[13px]">
          Time bucket
          <Select
            aria-label="Time bucket"
            value={builder.bucket}
            onValueChange={builder.setBucket}
            options={[
              { value: 'raw', label: 'As returned' },
              { value: 'minute', label: 'Minute (UTC)' },
              { value: 'hour', label: 'Hour (UTC)' },
              { value: 'day', label: 'Day (UTC)' },
            ]}
          />
        </label>
      ) : null}
      <div className="grid gap-1.5 text-[13px]">
        <span>{pivot ? 'Values' : 'Y axis'}</span>
        <div className="flex gap-1.5">
          <Select
            aria-label="Value"
            value={builder.y}
            onValueChange={builder.setY}
            options={builder.numeric.map((column) => ({
              value: column.name,
              label: column.name,
            }))}
          />
          <Select
            aria-label="Aggregation"
            value={builder.operation}
            onValueChange={builder.setOperation}
            options={operations}
            className="w-[88px] shrink-0"
          />
        </div>
      </div>
      <label className="grid gap-1.5 text-[13px]">
        {pivot ? 'Columns' : 'Group by / colour'}
        <Select
          aria-label={pivot ? 'Pivot columns' : 'Group by / colour'}
          value={builder.group}
          onValueChange={builder.setGroup}
          options={[{ value: '', label: 'No grouping' }, ...fieldOptions]}
        />
      </label>
    </>
  )
}

function ReturnedRowsNote({ result }: { result: QueryResult }) {
  return (
    <p className="m-0 border-t border-line pt-3 text-xs text-muted-foreground">
      Based on {result.rows.length} returned rows. Null values are excluded from
      numeric aggregates.
      {result.has_more
        ? ' Results are truncated; aggregates cover only the returned rows.'
        : ''}
    </p>
  )
}

function ChartSeries({
  type,
  data,
  labels,
}: {
  type: string
  data: Builder['data']
  labels: boolean
}) {
  return data.series.map((series, index) => {
    const props = {
      dataKey: series.key,
      name: series.label,
      fill: colors[index % colors.length],
      stroke: colors[index % colors.length],
      isAnimationActive: false,
    }
    const annotation = labels ? (
      <LabelList dataKey={series.key} position="top" />
    ) : null
    if (type === 'bar')
      return (
        <Bar key={series.key} {...props}>
          {annotation}
        </Bar>
      )
    if (type === 'area')
      return (
        <Area
          key={series.key}
          {...props}
          fillOpacity={0.12}
          connectNulls={false}
        >
          {annotation}
        </Area>
      )
    if (type === 'scatter')
      return (
        <Scatter key={series.key} {...props}>
          {annotation}
        </Scatter>
      )
    return (
      <Line key={series.key} {...props} dot={{ r: 2 }} connectNulls={false}>
        {annotation}
      </Line>
    )
  })
}

export function ResultsChart({
  result,
  config: supplied,
  onConfigChange,
}: BuilderProps) {
  const { config, update } = useConfiguration(supplied, onConfigChange)
  const builder = useResultBuilder(result, config, update)
  const { type, reference, lower, upper, labels } = config
  const validBand =
    lower.trim() !== '' &&
    upper.trim() !== '' &&
    Number.isFinite(Number(lower)) &&
    Number.isFinite(Number(upper)) &&
    Number(lower) < Number(upper)
  if (!builder.y)
    return (
      <p className="p-5 text-sm text-muted-foreground">
        No numeric columns are available to chart.
      </p>
    )
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row">
      <aside
        aria-label="Chart builder"
        className="grid w-full shrink-0 content-start gap-4 border-b border-line p-4 lg:w-[280px] lg:border-r lg:border-b-0"
      >
        <div className="grid gap-1.5 text-[13px]">
          <span>Chart type</span>
          <Segmented
            aria-label="Chart type"
            value={type}
            onValueChange={(nextType: QueryView['type']) =>
              update({ type: nextType })
            }
            options={[
              { value: 'line', label: 'Line' },
              { value: 'bar', label: 'Bar' },
              { value: 'area', label: 'Area' },
              { value: 'scatter', label: 'Scatter' },
            ]}
          />
        </div>
        <BuilderFields result={result} builder={builder} />
        <div className="grid gap-2 border-t border-line pt-3 text-[13px]">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={reference}
              onChange={(event) => update({ reference: event.target.checked })}
              className="accent-primary"
            />
            Reference band
          </label>
          {reference ? (
            <>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  aria-label="Reference lower limit"
                  type="number"
                  value={lower}
                  onChange={(event) => update({ lower: event.target.value })}
                  className="h-8 min-w-0 w-20 rounded border border-input bg-card px-2"
                />
                to
                <input
                  aria-label="Reference upper limit"
                  type="number"
                  value={upper}
                  onChange={(event) => update({ upper: event.target.value })}
                  className="h-8 min-w-0 w-20 rounded border border-input bg-card px-2"
                />
              </div>
              {!validBand ? (
                <p role="alert" className="m-0 text-bad-text">
                  Enter a lower limit below the upper limit.
                </p>
              ) : null}
              <p className="m-0 text-xs text-muted-foreground">
                User-defined limits; these do not change audit thresholds.
              </p>
            </>
          ) : null}
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={labels}
              onChange={(event) => update({ labels: event.target.checked })}
              className="accent-primary"
            />
            Value labels
          </label>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col gap-4 p-5">
        <h2 className="m-0 text-sm font-medium">
          {operations.find((item) => item.value === builder.operation)?.label}{' '}
          {builder.y} by {builder.x || 'row number'}
        </h2>
        <ResultPlot
          builder={builder}
          result={result}
          type={type}
          reference={reference && validBand}
          lower={lower}
          upper={upper}
          labels={labels}
        />
        <ReturnedRowsNote result={result} />
      </div>
    </div>
  )
}

function ResultPlot({
  builder,
  result,
  type,
  reference,
  lower,
  upper,
  labels,
}: {
  builder: Builder
  result: QueryResult
  type: QueryView['type']
  reference: boolean
  lower: string
  upper: string
  labels: boolean
}) {
  const scatter = scatterResultData(result, builder.x, builder.data.rows)
  if (type === 'scatter' && !scatter)
    return (
      <p
        role="status"
        className="m-0 flex min-h-[300px] items-center justify-center text-sm text-muted-foreground"
      >
        Scatter requires a numeric X axis. Choose a numeric column to plot.
      </p>
    )
  return (
    <ChartContainer
      config={Object.fromEntries(
        builder.data.series.map((series, index) => [
          series.key,
          { label: series.label, color: colors[index % colors.length] },
        ]),
      )}
      label={`${builder.operation} ${builder.y} by ${builder.x || 'row number'}${builder.group ? ` grouped by ${builder.group}` : ''}`}
      className="aspect-auto min-h-[300px] flex-1"
    >
      <ComposedChart
        data={type === 'scatter' ? scatter!.rows : builder.data.rows}
        margin={{ top: 20, right: 24, bottom: 8, left: 4 }}
      >
        <CartesianGrid vertical={false} />
        <XAxis
          {...(type === 'scatter' ? scatter!.xAxis : { dataKey: 'label' })}
          tickLine={false}
          axisLine={false}
          fontSize={11}
          interval="preserveStartEnd"
          minTickGap={24}
        />
        <YAxis tickLine={false} axisLine={false} fontSize={11} width={48} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Legend
          verticalAlign="top"
          align="left"
          wrapperStyle={{ paddingBottom: 16 }}
        />
        {reference ? (
          <>
            <ReferenceArea
              y1={Number(lower)}
              y2={Number(upper)}
              fill="var(--muted-foreground)"
              fillOpacity={0.08}
              ifOverflow="extendDomain"
            />
            <ReferenceLine y={Number(lower)} strokeDasharray="4 4" />
            <ReferenceLine y={Number(upper)} strokeDasharray="4 4" />
          </>
        ) : null}
        {ChartSeries({ type, data: builder.data, labels })}
      </ComposedChart>
    </ChartContainer>
  )
}

export function ResultsPivot({
  result,
  config: supplied,
  onConfigChange,
}: BuilderProps) {
  const { config, update } = useConfiguration(supplied, onConfigChange)
  const builder = useResultBuilder(result, config, update)
  if (!builder.y)
    return (
      <p className="p-5 text-sm text-muted-foreground">
        No numeric columns are available to pivot.
      </p>
    )
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row">
      <aside
        aria-label="Pivot builder"
        className="grid w-full shrink-0 content-start gap-4 border-b border-line p-4 lg:w-[280px] lg:border-r lg:border-b-0"
      >
        <BuilderFields result={result} builder={builder} pivot />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col gap-4 overflow-auto p-5">
        <h2 className="m-0 text-sm font-medium">
          {operations.find((item) => item.value === builder.operation)?.label}{' '}
          {builder.y}
        </h2>
        <table
          className="w-full border-collapse text-left text-xs tabular-nums"
          aria-label="Pivot results"
        >
          <thead>
            <tr className="border-b border-line text-muted-foreground">
              <th scope="col" className="p-2 font-medium">
                {builder.x || 'Row'}
              </th>
              {builder.data.series.map((series) => (
                <th
                  scope="col"
                  key={series.key}
                  className="p-2 text-right font-medium"
                >
                  {series.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {builder.data.rows.map((row) => (
              <tr key={row.label} className="border-b border-line-soft">
                <th scope="row" className="p-2 font-normal">
                  {row.label}
                </th>
                {builder.data.series.map((series) => {
                  const value = (row as Record<string, unknown>)[series.key]
                  return (
                    <td key={series.key} className="p-2 text-right font-mono">
                      {typeof value === 'number'
                        ? value.toLocaleString(undefined, {
                            maximumFractionDigits: 4,
                          })
                        : 'NULL'}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <ReturnedRowsNote result={result} />
      </div>
    </div>
  )
}
