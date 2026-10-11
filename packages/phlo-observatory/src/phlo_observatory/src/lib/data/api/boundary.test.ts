/** Negative regressions for provider I/O and duplicate backend responsibilities. */
import { describe, expect, it } from 'vitest'
// @ts-expect-error Shared executable Node guard.
import {
  checkSource,
  checkTree,
} from '../../../../scripts/adapter-boundary.mjs'

describe('the retained Node API adapter boundary', () => {
  it('permits current production code but not provider/database/source-of-truth access', () => {
    expect(checkTree('src')).toEqual([])
    const file = '/src/lib/data/api/new-adapter.ts'
    for (const source of [
      "import { Pool } from 'pg'; new Pool().query('SELECT * FROM runs')",
      "fetch('http://dagster/graphql', {method: 'POST'})",
      'const token = process.env.PHLO_SERVICE_TOKEN',
      "phloApi('http://nessie/api/v2/trees', schema)",
      "const sql = 'INSERT INTO query_sessions VALUES ($1)'",
      "const pg = await import('postgres')",
    ])
      expect(checkSource(source, file), source).not.toEqual([])
    expect(
      checkSource(
        'createServerFn({method: "POST"}).handler(() => ({}))',
        '/src/server/duplicate.ts',
      ),
    ).not.toEqual([])
    expect(checkSource("phloApi('api/v1/runs', runSchema)", file)).toEqual([])
  })
})
