/**
 * Tests SQL parsing helpers: source-table extraction, column mappings,
 * transformation analysis, and smart where-clause building.
 */
import { describe, expect, it } from 'vitest'

import {
  analyzeSQLTransformation,
  buildSmartWhereClause,
  extractSourceTables,
  parseColumnMappings,
} from './sqlParser'

describe('sqlParser', () => {
  it('extracts source table from compiled dbt SQL with quoted identifiers', () => {
    const compiledDbtSql = `
      select
        count(*) as total_events
      from "iceberg"."bronze"."fct_github_events"
      where event_date > (select coalesce(max(activity_date), date('1900-01-01')) from "iceberg"."bronze"."fct_daily_github_metrics")
    `

    const result = extractSourceTables(compiledDbtSql)
    expect(result).toContain('fct_github_events')
  })

  it('extracts table name from simple unquoted SQL', () => {
    const simpleSql = `SELECT * FROM my_table WHERE id = 1`
    expect(extractSourceTables(simpleSql)).toContain('my_table')
  })

  it('extracts table name with EXTRACT function present', () => {
    const extractSql = `SELECT extract(month from date_column) as month FROM users`
    const tables = extractSourceTables(extractSql)
    expect(tables).toContain('users')
    expect(tables).not.toContain('date_column')
  })

  it('extracts joined tables', () => {
    const joinSql = `
      SELECT a.*, b.name
      FROM "iceberg"."gold"."orders" a
      JOIN "iceberg"."gold"."customers" b ON a.customer_id = b.id
    `
    const tables = extractSourceTables(joinSql)
    expect(tables).toContain('orders')
    expect(tables).toContain('customers')
  })

  it('extracts ref() target in raw dbt SQL', () => {
    const rawDbtSql = `
      select
        event_date as activity_date,
        count(*) as total_events
      from {{ ref('fct_github_events') }}
      group by event_date
    `
    expect(extractSourceTables(rawDbtSql)).toContain('fct_github_events')
  })

  it('extracts simple table name in raw SQL', () => {
    const rawDbtNoRef = `select * from my_source_table where id > 0`
    expect(extractSourceTables(rawDbtNoRef)).toContain('my_source_table')
  })

  it('extracts column mappings from compiled SQL', () => {
    const compiledDbtSql = `
      select
        event_date as activity_date,
        extract(month from event_date) as month,
        count(*) as total_events
      from "iceberg"."bronze"."fct_github_events"
    `
    const mappings = parseColumnMappings(compiledDbtSql)
    expect(
      mappings.find((m) => m.targetColumn === 'activity_date')?.sourceColumn,
    ).toBe('event_date')
  })

  it('analyzes SQL transformation', () => {
    const compiledDbtSql = `
      select
        event_date as activity_date,
        count(*) as total_events
      from "iceberg"."bronze"."fct_github_events"
    `
    const analysis = analyzeSQLTransformation(compiledDbtSql)
    expect(analysis.sourceTables).toContain('fct_github_events')
    expect(analysis.columnMappings.length).toBeGreaterThan(0)
  })

  it('builds smart WHERE clause using source mappings', () => {
    const compiledDbtSql = `
      select
        event_date as activity_date,
        count(*) as total_events
      from "iceberg"."bronze"."fct_github_events"
    `
    const analysis = analyzeSQLTransformation(compiledDbtSql)

    const sampleRowData = {
      activity_date: '2025-12-02',
      total_events: 42,
    }
    const { whereClause } = buildSmartWhereClause(
      sampleRowData,
      analysis.columnMappings,
    )
    expect(whereClause).toMatch(/event_date|activity_date/)
  })
})
