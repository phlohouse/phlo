/** Aggregates returned query rows without inventing values for missing groups. */
import type { QueryResult } from './data/api/query'

export type ResultAggregation = 'avg' | 'sum' | 'min' | 'max' | 'count'
export const resultLabel = (value: unknown) =>
  value === null || value === undefined
    ? 'NULL'
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value)

export function numericResultColumns(result: QueryResult) {
  return result.columns.filter(
    (column) =>
      result.rows.some((row) => typeof row[column.name] === 'number') &&
      result.rows.every(
        (row) =>
          row[column.name] === null || typeof row[column.name] === 'number',
      ),
  )
}

function aggregate(values: Array<number>, operation: ResultAggregation) {
  if (operation === 'count') return values.length
  if (!values.length) return null
  if (operation === 'min') return Math.min(...values)
  if (operation === 'max') return Math.max(...values)
  const sum = values.reduce((total, value) => total + value, 0)
  return operation === 'avg' ? sum / values.length : sum
}

export function aggregateQueryResult(
  result: QueryResult,
  x: string,
  y: string,
  group: string,
  operation: ResultAggregation,
  timeBucket: 'raw' | 'minute' | 'hour' | 'day' = 'raw',
) {
  const temporal = /date|timestamp/i.test(
    result.columns.find((column) => column.name === x)?.type ?? '',
  )
  const series = new Map<string, { key: string; value: unknown }>()
  const axes = new Map<string, unknown>()
  const groups = new Map<string, Map<string, Array<number>>>()
  for (const [index, row] of result.rows.entries()) {
    const axis = x
      ? bucketResultValue(row[x], temporal ? timeBucket : 'raw')
      : String(index + 1)
    const identity = valueIdentity(axis)
    axes.set(identity, axis)
    const category = group ? row[group] : y
    const categoryIdentity = valueIdentity(category)
    if (!series.has(categoryIdentity))
      series.set(categoryIdentity, {
        key: `series${series.size}`,
        value: category,
      })
    const key = series.get(categoryIdentity)!.key
    const bucket = groups.get(identity) ?? new Map<string, Array<number>>()
    const values = bucket.get(key) ?? []
    const value = row[y]
    if (typeof value === 'number' && Number.isFinite(value)) values.push(value)
    bucket.set(key, values)
    groups.set(identity, bucket)
  }
  const axisLabels = distinctLabels(axes)
  const seriesLabels = distinctLabels(
    new Map([...series].map(([identity, item]) => [identity, item.value])),
  )
  return {
    series: [...series].map(([identity, item]) => ({
      label: seriesLabels.get(identity)!,
      key: item.key,
    })),
    rows: [...groups].map(([identity, bucket]) => ({
      label: axisLabels.get(identity)!,
      ...Object.fromEntries(
        [...series.values()].map(({ key }) => [
          key,
          bucket.has(key) ? aggregate(bucket.get(key)!, operation) : null,
        ]),
      ),
    })),
  }
}

function valueIdentity(value: unknown) {
  return JSON.stringify([value === null ? 'null' : typeof value, value])
}
function distinctLabels(values: Map<string, unknown>) {
  const labels = [...values].map(([identity, value]) => ({
    identity,
    value,
    label: resultLabel(value),
  }))
  const counts = new Map<string, number>()
  for (const { label } of labels)
    counts.set(label, (counts.get(label) ?? 0) + 1)
  const used = new Set<string>()
  return new Map(
    labels.map(({ identity, value, label }) => {
      const kind =
        value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value
      let proposed = counts.get(label)! > 1 ? `${label} (${kind})` : label
      let sequence = 1
      while (used.has(proposed)) proposed = `${label} (${kind}, ${++sequence})`
      used.add(proposed)
      return [identity, proposed]
    }),
  )
}

function bucketResultValue(
  value: unknown,
  bucket: 'raw' | 'minute' | 'hour' | 'day',
) {
  if (bucket === 'raw' || typeof value !== 'string') return value
  // Timestamp strings without an offset use UTC, matching the displayed bucket labels.
  const timestamp = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
    ? value
    : `${value.replace(' ', 'T')}Z`
  const date = new Date(timestamp)
  if (!Number.isFinite(date.getTime())) return value
  if (bucket === 'day') date.setUTCHours(0, 0, 0, 0)
  else if (bucket === 'hour') date.setUTCMinutes(0, 0, 0)
  else date.setUTCSeconds(0, 0)
  return date.toISOString()
}

/** Scatter axes use numeric source values; display labels are never parsed as coordinates. */
export function scatterResultData(
  result: QueryResult,
  x: string,
  rows: ReturnType<typeof aggregateQueryResult>['rows'],
) {
  if (!numericResultColumns(result).some((column) => column.name === x))
    return null
  const coordinates = new Map<string, number>()
  for (const row of result.rows) {
    const value = row[x]
    if (typeof value === 'number' && Number.isFinite(value))
      coordinates.set(resultLabel(value), value)
  }
  return {
    rows: rows.flatMap((row) => {
      const value = coordinates.get(row.label)
      return value === undefined ? [] : [{ ...row, xValue: value }]
    }),
    xAxis: {
      dataKey: 'xValue',
      type: 'number' as const,
      domain: ['dataMin', 'dataMax'],
    },
  }
}
