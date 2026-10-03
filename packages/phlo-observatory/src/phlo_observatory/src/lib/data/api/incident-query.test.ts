/** Tests incident/query requests without accepting browser-generated evidence. */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { z } from 'zod'

import { phloApi } from './client'
import { getAssetDetail } from './assets'
import { getIncidentDetail } from './incidents'
import { pinQueryToIncident, submitQuery } from './query'

vi.mock('@tanstack/react-start', () => ({
  createServerFn: () => {
    let schema: z.ZodType
    const builder = {
      inputValidator(value: z.ZodType) {
        schema = value
        return builder
      },
      validator(value: z.ZodType) {
        schema = value
        return builder
      },
      handler(handler: (args: { data: unknown }) => unknown) {
        return (args: { data: unknown }) =>
          handler({ data: schema.parse(args.data) })
      },
    }
    return builder
  },
}))
vi.mock('./client', () => ({
  environmentSchema: z.enum(['prod', 'staging']),
  phloApi: vi.fn(),
}))
vi.mock('./assets', () => ({ getAssetDetail: vi.fn() }))
vi.mock('./pipelines', () => ({
  runSchema: z.object({
    run_id: z.string(),
    selected_assets: z.array(z.array(z.string())),
  }),
}))

const incident = {
  id: 'incident-7',
  asset_id: 'bronze/orders',
  asset_ids: ['bronze/orders'],
  kind: 'schema',
  title: 'Schema changed',
  status: 'open',
  owner: null,
  version: 4,
  created_at: '2026-10-02T12:00:00Z',
  updated_at: '2026-10-02T12:00:00Z',
}

beforeEach(() => vi.resetAllMocks())

describe('Incident query parity contracts', () => {
  it('pins only the execution ID with environment, revision and replay key', async () => {
    vi.mocked(phloApi).mockResolvedValue(incident)
    await pinQueryToIncident({
      data: {
        env: 'staging',
        id: 'execution-12',
        incident_id: incident.id,
        version: 4,
        idempotency_key: 'pin-replay',
      },
    })
    expect(phloApi).toHaveBeenCalledWith(
      'api/v1/incidents/incident-7?env=staging',
      expect.anything(),
      expect.objectContaining({
        method: 'PATCH',
        body: { query_id: 'execution-12' },
        headers: { 'if-match': '4' },
        idempotencyKey: 'pin-replay',
      }),
    )
  })

  it('preserves exact submitted SQL and never relabels Trino as DuckDB', async () => {
    const sql =
      'SELECT *\nFROM bronze.orders WHERE amount > 81.5 -- supplied preview SQL'
    vi.mocked(phloApi).mockResolvedValue({})
    await submitQuery({ data: { env: 'staging', sql } })
    expect(phloApi).toHaveBeenCalledWith(
      '/api/v1/queries?env=staging',
      expect.anything(),
      expect.objectContaining({
        body: { sql, row_limit: 100, engine: 'trino' },
      }),
    )
    expect(() =>
      submitQuery({ data: { env: 'staging', sql, engine: 'duckdb' } } as never),
    ).toThrow()
    expect(phloApi).toHaveBeenCalledTimes(1)
  })

  it('loads specialised schema investigation and only related environment-scoped runs', async () => {
    vi.mocked(phloApi).mockImplementation((path, schema) =>
      schema.parseAsync(
        path.includes('/timeline') || path.includes('/follow-ups')
          ? { items: [] }
          : path.startsWith('api/v1/runs?')
            ? {
                env: 'staging',
                next_cursor: null,
                items: [
                  {
                    run_id: 'related',
                    selected_assets: [['bronze', 'orders']],
                  },
                  {
                    run_id: 'unrelated',
                    selected_assets: [['gold', 'customers']],
                  },
                ],
              }
            : incident,
      ),
    )
    vi.mocked(getAssetDetail).mockResolvedValue({
      kind: 'schema',
      data: { items: [] },
    } as never)
    const detail = await getIncidentDetail({
      data: { env: 'staging', id: incident.id },
    })
    expect(getAssetDetail).toHaveBeenCalledWith({
      data: { env: 'staging', id: 'bronze/orders', tab: 'schema' },
    })
    expect(detail.runs.items.map((run) => run.run_id)).toEqual(['related'])
    expect(detail.investigation.kind).toBe('schema')
  })
})
