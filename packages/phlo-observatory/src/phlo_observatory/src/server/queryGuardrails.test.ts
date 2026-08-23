/**
 * Tests SQL guardrails: statement splitting, read-only enforcement, limit
 * injection, and unsafe-query rejection.
 */
import { describe, expect, it } from 'vitest'

import {
  splitSqlStatements,
  validateAndRewriteQuery,
} from '@/server/queryGuardrails'

describe('query guardrails', () => {
  it('rejects empty input', () => {
    const result = validateAndRewriteQuery({
      query: '   ',
      guardrails: { readOnlyMode: true, defaultLimit: 100, maxLimit: 5000 },
      allowUnsafe: false,
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('invalid')
  })

  it('rejects multi-statement queries', () => {
    const result = validateAndRewriteQuery({
      query: 'select 1; select 2',
      guardrails: { readOnlyMode: true, defaultLimit: 100, maxLimit: 5000 },
      allowUnsafe: false,
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('blocked')
  })

  it('does not split semicolons in strings', () => {
    expect(splitSqlStatements("select 'a;b'")).toHaveLength(1)
  })

  it('blocks DML in read-only mode', () => {
    const result = validateAndRewriteQuery({
      query: 'delete from iceberg.gold.table where id = 1',
      guardrails: { readOnlyMode: true, defaultLimit: 100, maxLimit: 5000 },
      allowUnsafe: false,
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('blocked')
  })

  it('requires confirmation for unsafe statements when read-only is off', () => {
    const result = validateAndRewriteQuery({
      query: 'delete from iceberg.gold.table where id = 1',
      guardrails: { readOnlyMode: false, defaultLimit: 100, maxLimit: 5000 },
      allowUnsafe: false,
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.kind).toBe('confirm_required')
  })

  it('adds limit guardrails for SELECT', () => {
    const result = validateAndRewriteQuery({
      query: 'select * from iceberg.gold.table',
      guardrails: { readOnlyMode: true, defaultLimit: 123, maxLimit: 456 },
      allowUnsafe: false,
    })
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.effectiveQuery).toMatch(/LIMIT 456\s*$/)
  })

  it('allows WITH SELECT and blocks WITH + forbidden keyword', () => {
    const ok = validateAndRewriteQuery({
      query: 'with t as (select 1) select * from t',
      guardrails: { readOnlyMode: true, defaultLimit: 10, maxLimit: 10 },
      allowUnsafe: false,
    })
    expect(ok.ok).toBe(true)

    const blocked = validateAndRewriteQuery({
      query: 'with t as (select 1) insert into x select * from t',
      guardrails: { readOnlyMode: true, defaultLimit: 10, maxLimit: 10 },
      allowUnsafe: false,
    })
    expect(blocked.ok).toBe(false)
    if (blocked.ok) return
    expect(blocked.kind).toBe('blocked')
  })
})
