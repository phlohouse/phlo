/** Verifies response validation, environment isolation, and exact Iceberg integer digits. */
import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import { parseApiResponse } from './client'

describe('Phlo API response boundary', () => {
  const snapshot = z.object({
    env: z.enum(['prod', 'staging']),
    snapshot_id: z.string(),
    rows: z.number().int(),
  })

  it('preserves unsafe integer digits while keeping safe integers numeric', async () => {
    const result = await parseApiResponse({
      response: new Response(
        '{"env":"staging","snapshot_id":9007199254740993,"rows":7}',
      ),
      schema: snapshot,
      env: 'staging',
    })
    expect(result).toEqual({
      env: 'staging',
      snapshot_id: '9007199254740993',
      rows: 7,
    })
  })

  it('rejects valid data returned for the wrong environment', async () => {
    await expect(
      parseApiResponse({
        response: Response.json({ env: 'prod', snapshot_id: '42', rows: 3 }),
        schema: snapshot,
        env: 'staging',
      }),
    ).rejects.toThrow('Phlo API returned data for a different environment.')
  })

  it('rejects a response that does not satisfy the declared schema', async () => {
    await expect(
      parseApiResponse({
        response: Response.json({
          env: 'staging',
          snapshot_id: '42',
          rows: '7',
        }),
        schema: snapshot,
        env: 'staging',
      }),
    ).rejects.toThrow('Phlo API response did not match the expected contract.')
  })

  it('rejects malformed JSON rather than returning empty data', async () => {
    await expect(
      parseApiResponse({
        response: new Response('{"env":'),
        schema: snapshot,
      }),
    ).rejects.toThrow('Phlo API returned invalid JSON.')
  })

  it('returns CSV text without trying to parse it as JSON', async () => {
    const csv = 'order_id,status\nO-7,fulfilled\n'
    await expect(
      parseApiResponse({
        response: new Response(csv),
        schema: z.string(),
        responseType: 'text',
      }),
    ).resolves.toBe(csv)
  })
})
