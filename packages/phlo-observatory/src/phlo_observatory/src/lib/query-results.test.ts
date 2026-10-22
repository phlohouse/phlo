/** Verifies chart and pivot aggregates reflect only the returned numeric values. */
import { describe, expect, it } from 'vitest'
import {
  aggregateQueryResult,
  numericResultColumns,
  scatterResultData,
} from './query-results'
import type { QueryResult } from './data/api/query'

const result: QueryResult = {
  columns: ['week', 'product', 'potency'].map((name) => ({ name, type: null })),
  rows: [
    { week: '2026-07-06', product: 'CGT-02', potency: 90 },
    { week: '2026-07-06', product: 'CGT-02', potency: 110 },
    { week: '2026-07-06', product: 'CGT-04', potency: 80 },
    { week: '2026-07-13', product: 'CGT-04', potency: null },
    { week: '2026-07-20', product: 'CGT-04', potency: 85 },
  ],
  has_more: true,
}

describe('query result builder', () => {
  it('averages repeated X values within each series without filling missing groups with zero', () => {
    expect(
      aggregateQueryResult(result, 'week', 'potency', 'product', 'avg'),
    ).toEqual({
      series: [
        { key: 'series0', label: 'CGT-02' },
        { key: 'series1', label: 'CGT-04' },
      ],
      rows: [
        { label: '2026-07-06', series0: 100, series1: 80 },
        { label: '2026-07-13', series0: null, series1: null },
        { label: '2026-07-20', series0: null, series1: 85 },
      ],
    })
  })

  it.each([
    ['sum', 200],
    ['min', 90],
    ['max', 110],
    ['count', 2],
  ] as const)(
    'computes %s using only returned values',
    (operation, expected) => {
      expect(
        aggregateQueryResult(result, 'week', 'potency', 'product', operation)
          .rows[0],
      ).toMatchObject({ series0: expected })
    },
  )

  it('counts null cells as zero observations while leaving absent groups null', () => {
    expect(
      aggregateQueryResult(result, 'week', 'potency', 'product', 'count')
        .rows[1],
    ).toEqual({ label: '2026-07-13', series0: null, series1: 0 })
  })

  it('keeps individual rows when row number is selected', () => {
    const data = aggregateQueryResult(result, '', 'potency', '', 'avg')
    expect(data.rows).toHaveLength(5)
    expect(data.rows[0]).toEqual({ label: '1', series0: 90 })
  })

  it('accepts nullable numeric columns and rejects mixed string and numeric columns', () => {
    expect(numericResultColumns(result).map((column) => column.name)).toEqual([
      'potency',
    ])
    const mixed = {
      ...result,
      rows: [{ week: 'one', product: 'CGT', potency: '90' }, ...result.rows],
    }
    expect(numericResultColumns(mixed)).toEqual([])
  })

  it('uses internal series keys for user-controlled group names', () => {
    const unusual = {
      ...result,
      rows: [
        { week: 'one', product: '__proto__', potency: 10 },
        { week: 'one', product: 'a.b', potency: 20 },
      ],
    }
    expect(
      aggregateQueryResult(unusual, 'week', 'potency', 'product', 'avg')
        .rows[0],
    ).toEqual({ label: 'one', series0: 10, series1: 20 })
  })
  it('buckets temporal strings in UTC without coercing numeric dimensions into dates', () => {
    const temporal: QueryResult = {
      columns: [
        { name: 'ts', type: 'timestamp' },
        { name: 'value', type: 'double' },
      ],
      rows: [
        { ts: '2026-10-22 10:01:00', value: 10 },
        { ts: '2026-10-22T11:02:00+01:00', value: 20 },
      ],
      has_more: false,
    }
    expect(
      aggregateQueryResult(temporal, 'ts', 'value', '', 'avg', 'hour').rows,
    ).toEqual([{ label: '2026-10-22T10:00:00.000Z', series0: 15 }])
    const numeric = {
      ...temporal,
      columns: [
        { name: 'ts', type: 'bigint' },
        { name: 'value', type: 'double' },
      ],
      rows: [
        { ts: 1, value: 10 },
        { ts: 2, value: 20 },
      ],
    }
    expect(
      aggregateQueryResult(numeric, 'ts', 'value', '', 'avg', 'day').rows,
    ).toHaveLength(2)
  })
  it('keeps null and the literal NULL separate on both pivot axes', () => {
    const nullable: QueryResult = {
      columns: [
        { name: 'x', type: 'varchar' },
        { name: 'category', type: 'varchar' },
        { name: 'value', type: 'double' },
      ],
      rows: [
        { x: null, category: null, value: 10 },
        { x: 'NULL', category: 'NULL', value: 20 },
      ],
      has_more: false,
    }
    expect(
      aggregateQueryResult(nullable, 'x', 'value', 'category', 'avg'),
    ).toEqual({
      series: [
        { key: 'series0', label: 'NULL (null)' },
        { key: 'series1', label: 'NULL (string)' },
      ],
      rows: [
        { label: 'NULL (null)', series0: 10, series1: null },
        { label: 'NULL (string)', series0: null, series1: 20 },
      ],
    })
  })
  it('keeps numeric and string groups distinct and disambiguates colliding literal labels', () => {
    const mixed: QueryResult = {
      columns: [
        { name: 'x', type: 'varchar' },
        { name: 'value', type: 'double' },
      ],
      rows: [
        { x: 1, value: 10 },
        { x: '1', value: 20 },
        { x: '1 (number)', value: 30 },
      ],
      has_more: false,
    }
    const data = aggregateQueryResult(mixed, 'x', 'value', '', 'sum')
    expect(
      data.rows.map((row) => (row as Record<string, unknown>).series0),
    ).toEqual([10, 20, 30])
    expect(new Set(data.rows.map((row) => row.label)).size).toBe(3)
    expect(data.rows[0].label).toBe('1 (number)')
    expect(data.rows[1].label).toBe('1 (string)')
  })
  it('uses true numeric scatter coordinates and a numeric axis domain', () => {
    const numeric: QueryResult = {
      columns: [
        { name: 'x', type: 'double' },
        { name: 'value', type: 'double' },
      ],
      rows: [
        { x: 1, value: 10 },
        { x: 2, value: 20 },
        { x: 1000, value: 30 },
        { x: null, value: 40 },
      ],
      has_more: false,
    }
    const aggregate = aggregateQueryResult(numeric, 'x', 'value', '', 'avg')
    const scatter = scatterResultData(numeric, 'x', aggregate.rows)
    expect(scatter?.rows.map((row) => row.xValue)).toEqual([1, 2, 1000])
    expect(scatter?.xAxis).toEqual({
      type: 'number',
      dataKey: 'xValue',
      domain: ['dataMin', 'dataMax'],
    })
    expect(scatterResultData(result, 'week', [])).toBeNull()
  })
})
