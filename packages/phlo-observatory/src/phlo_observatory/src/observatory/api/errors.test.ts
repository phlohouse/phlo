/**
 * Tests for the phlo-api error helpers (errors.ts): the thrown
 * `phlo-api error: <status> <body>` contract shared by the server transport
 * and the browser fallbacks.
 */
import { describe, expect, it } from 'vitest'

import { describePhloApiError, readPhloApiErrorBody } from './errors'

const ENVELOPE_BODY = JSON.stringify({
  error: {
    code: 'backend_unavailable',
    message: 'Observability backend is unavailable.',
  },
})

describe('readPhloApiErrorBody', () => {
  it('reads the typed error envelope', () => {
    expect(readPhloApiErrorBody(JSON.parse(ENVELOPE_BODY))).toEqual({
      code: 'backend_unavailable',
      message: 'Observability backend is unavailable.',
    })
  })

  it('reads a legacy string detail', () => {
    expect(readPhloApiErrorBody({ detail: 'nope' })).toEqual({
      message: 'nope',
    })
  })

  it('returns null for bodies without a readable error', () => {
    expect(readPhloApiErrorBody('plain text')).toBeNull()
    expect(readPhloApiErrorBody({})).toBeNull()
  })
})

describe('describePhloApiError', () => {
  it('unwraps the JSON envelope appended by the server transport', () => {
    const error = new Error(`phlo-api error: 503 ${ENVELOPE_BODY}`)
    expect(describePhloApiError(error)).toEqual({
      status: 503,
      code: 'backend_unavailable',
      message: 'Observability backend is unavailable.',
    })
  })

  it('recovers status and message from the browser-fallback plain-text form', () => {
    const error = new Error(
      'phlo-api error: 503 Observability backend is unavailable.',
    )
    expect(describePhloApiError(error)).toEqual({
      status: 503,
      code: undefined,
      message: 'Observability backend is unavailable.',
    })
  })

  it('recovers status and statusText from mutations that had no envelope', () => {
    const error = new Error('phlo-api error: 404 Not Found')
    expect(describePhloApiError(error)).toEqual({
      status: 404,
      code: undefined,
      message: 'Not Found',
    })
  })

  it('passes through non-prefixed error messages unchanged', () => {
    expect(
      describePhloApiError(new Error('phlo-api request timed out')),
    ).toEqual({
      message: 'phlo-api request timed out',
    })
  })

  it('falls back to a generic message for non-Error values', () => {
    expect(describePhloApiError('offline')).toEqual({
      message: 'Lakehouse API is unavailable',
    })
  })
})
