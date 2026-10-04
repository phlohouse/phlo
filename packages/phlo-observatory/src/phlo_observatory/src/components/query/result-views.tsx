/** Presents query results as a data grid or execution plan. */
import * as React from 'react'
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'
import type { QueryResult } from '@/lib/data/api/query'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'

const th =
  'sticky top-0 z-10 h-[34px] border-r border-b border-line bg-raised px-3 text-left font-sans text-xs font-normal whitespace-nowrap text-muted-foreground'
const td =
  'h-8 max-w-[420px] overflow-hidden text-ellipsis border-r border-b border-line-soft px-3 whitespace-nowrap text-text-2'
const display = (value: unknown) =>
  value === null
    ? 'NULL'
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value)

export function ResultsGrid({ result }: { result: QueryResult }) {
  return (
    <div
      role="region"
      aria-label="Query result table"
      tabIndex={0}
      className="min-h-0 flex-1 overflow-auto focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
    >
      <table
        className="w-full min-w-max border-separate border-spacing-0 font-mono text-[12.5px]"
        aria-label="Query results"
      >
        <thead>
          <tr>
            <th scope="col" className={`${th} text-right`}>
              #
            </th>
            {result.columns.map((column) => (
              <th key={column.name} scope="col" className={th}>
                {column.name}{' '}
                <span className="text-[10.5px] text-faint">
                  {column.type ?? 'unknown'}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, index) => (
            <tr key={index} className="hover:bg-raised">
              <td className={`${td} text-right text-faint`}>{index + 1}</td>
              {result.columns.map((column) => (
                <td
                  key={column.name}
                  className={td}
                  title={display(row[column.name])}
                >
                  {display(row[column.name])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function PlanView({ result }: { result: QueryResult }) {
  return (
    <div
      role="region"
      aria-label="Query plan"
      tabIndex={0}
      className="min-h-0 flex-1 overflow-auto focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
    >
      <pre className="m-0 px-4 py-4 font-mono text-[12.5px] leading-6 whitespace-pre-wrap text-text-2">
        {result.rows
          .map((row) =>
            result.columns
              .map((column) => display(row[column.name]))
              .join('\n'),
          )
          .join('\n')}
      </pre>
    </div>
  )
}

export function numericColumns(result: QueryResult) {
  return result.columns.filter(
    (column) =>
      result.rows.some((row) => typeof row[column.name] === 'number') &&
      result.rows.every(
        (row) =>
          row[column.name] === null || typeof row[column.name] === 'number',
      ),
  )
}

export function ResultsChart({ result }: { result: QueryResult }) {
  const numeric = numericColumns(result)
  const [selected, setSelected] = React.useState(numeric[0]?.name ?? '')
  const column = numeric.find((item) => item.name === selected) ?? numeric[0]
  const labels = result.columns.find(
    (item) => !numeric.some((number) => number.name === item.name),
  )
  if (!column)
    return (
      <div className="p-5 text-sm text-muted-foreground">
        No numeric columns are available to chart.
      </div>
    )
  const rows = result.rows.map((row, index) => ({
    label: labels ? display(row[labels.name]) : String(index + 1),
    value: row[column.name],
  }))
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-5">
      <label className="flex items-center gap-3 text-[13px] text-muted-foreground">
        Value
        <select
          value={column.name}
          onChange={(event) => setSelected(event.target.value)}
          className="h-8 rounded-md border border-border bg-card px-2 text-foreground"
        >
          {numeric.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <ChartContainer
        config={{ value: { label: column.name, color: 'var(--primary)' } }}
        label={`${column.name} across ${result.rows.length} returned rows`}
        className="aspect-auto h-[280px] w-full"
      >
        <LineChart
          data={rows}
          margin={{ top: 12, right: 36, bottom: 0, left: 12 }}
        >
          <CartesianGrid vertical={false} />
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            fontSize={11}
            interval="preserveStartEnd"
            minTickGap={24}
            tickFormatter={(value: string) =>
              value.length > 14 ? `${value.slice(0, 11)}…` : value
            }
          />
          <YAxis tickLine={false} axisLine={false} fontSize={11} width={48} />
          <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
          <Line
            dataKey="value"
            stroke="var(--color-value)"
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ChartContainer>
      <p className="m-0 text-xs text-muted-foreground">
        Returned rows only
        {result.has_more ? '. More rows are available from the API' : ''}.
      </p>
    </div>
  )
}
