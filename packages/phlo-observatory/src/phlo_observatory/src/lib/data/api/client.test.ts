/** Verifies response validation, environment isolation, and exact Iceberg integer digits. */
import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import {
  environmentServices,
  parseApiResponse,
  serviceHealthLabel,
  servicesSchema,
} from './client'

describe('Phlo API response boundary', () => {
  const snapshot = z.object({
    env: z.enum(['prod', 'staging']),
    snapshot_id: z.string(),
    rows: z.number().int(),
  })

  it('shows the public backend-unavailable message and retains the HTTP status', async () => {
    await expect(
      parseApiResponse({
        response: new Response('private backend details', { status: 403 }),
        schema: snapshot,
      }),
    ).rejects.toThrow('Your account does not have permission')
    await expect(
      parseApiResponse({
        response: Response.json(
          {
            error: {
              code: 'backend_unavailable',
              message:
                'Check definitions cannot be isolated: repository-local hasAssetChecks proof is missing or another repository declares checks for this asset. Use a workspace with uniquely scoped check definitions.',
            },
          },
          { status: 503 },
        ),
        schema: snapshot,
      }),
    ).rejects.toThrow(
      'Phlo API request failed (503). Check definitions cannot be isolated: repository-local hasAssetChecks proof is missing or another repository declares checks for this asset. Use a workspace with uniquely scoped check definitions.',
    )
    await expect(
      parseApiResponse({
        response: Response.json(
          {
            error: { code: 'backend_unavailable', message: 'Check scope.' },
          },
          { status: 503 },
        ),
        schema: snapshot,
      }),
    ).rejects.toMatchObject({ status: 503, name: 'PhloApiError' })
    await expect(
      parseApiResponse({
        response: new Response('private backend details', { status: 503 }),
        schema: snapshot,
      }),
    ).rejects.toThrow('Phlo API request failed (503).')
  })

  it('keeps malformed, oversized, and non-public error payloads generic', async () => {
    await expect(
      parseApiResponse({
        response: new Response('{"error":', { status: 503 }),
        schema: snapshot,
      }),
    ).rejects.toThrow('Phlo API request failed (503).')
    await expect(
      parseApiResponse({
        response: Response.json(
          {
            error: {
              code: 'backend_unavailable',
              message: `This should not be shown ${'x'.repeat(300)}`,
            },
          },
          { status: 503 },
        ),
        schema: snapshot,
      }),
    ).rejects.toThrow('Phlo API request failed (503).')
    await expect(
      parseApiResponse({
        response: Response.json(
          {
            error: {
              code: 'internal_error',
              message: 'Private stack trace and credentials',
            },
          },
          { status: 503 },
        ),
        schema: snapshot,
      }),
    ).rejects.toThrow('Phlo API request failed (503).')
    await expect(
      parseApiResponse({
        response: Response.json(
          {
            error: {
              code: 'backend_unavailable',
              message: 'Internal authorization=Bearer private-token',
            },
          },
          { status: 503 },
        ),
        schema: snapshot,
      }),
    ).rejects.toThrow('Phlo API request failed (503).')
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

describe('service health setup labels', () => {
  it('distinguishes missing bindings and failed configured probes', () => {
    const items = servicesSchema.parse({
      env: 'staging',
      items: [
        {
          id: 'trino',
          status: 'unknown',
          observed_at: null,
          response_time_seconds: null,
          reason: 'no_environment_binding',
        },
        {
          id: 'catalog-api',
          status: 'unavailable',
          observed_at: '2026-10-03T10:00:00Z',
          response_time_seconds: 0.1,
          reason: 'health_probe_failed',
        },
        {
          id: 'shared-platform',
          status: 'unavailable',
          observed_at: '2026-10-03T10:00:00Z',
          response_time_seconds: 5,
          reason: 'health_probe_deadline_exceeded',
        },
        {
          id: 'nessie',
          status: 'unknown',
          observed_at: null,
          response_time_seconds: null,
          reason: 'probe_not_configured',
        },
        {
          id: 'unused-plugin',
          status: 'inactive',
          observed_at: null,
          response_time_seconds: null,
          reason: 'disabled',
        },
      ],
      next_cursor: null,
    }).items

    expect(serviceHealthLabel(items[0])).toBe('not bound')
    expect(serviceHealthLabel(items[1])).toBe('probe failed')
    expect(serviceHealthLabel(items[2])).toBe('probe timed out')
    expect(environmentServices(items).map((service) => service.id)).toEqual([
      'catalog-api',
      'shared-platform',
      'nessie',
    ])
  })
})
