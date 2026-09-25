/**
 * phlo-api error helpers.
 *
 * phlo-api reports failures through HTTP status codes and a single typed
 * error body: `{"error": {"code": "...", "message": "..."}}`. The transport
 * helpers (`@/server/phlo-api` and the browser fallbacks in this directory)
 * throw `Error`s whose message is `phlo-api error: <status> <body>`, so these
 * helpers recover the status and envelope text either from a parsed response
 * payload or from a thrown error message.
 */

export interface PhloApiErrorInfo {
  status?: number
  code?: string
  message: string
}

/**
 * Extract the envelope message (or legacy FastAPI `detail`) from a parsed
 * error response body. Returns null when the body carries no readable error.
 */
export function readPhloApiErrorBody(
  payload: unknown,
): { code?: string; message: string } | null {
  if (!payload || typeof payload !== 'object') return null
  const record = payload as Record<string, unknown>
  const envelope = record.error
  if (envelope && typeof envelope === 'object') {
    const entry = envelope as Record<string, unknown>
    if (typeof entry.message === 'string' && entry.message) {
      return {
        code: typeof entry.code === 'string' ? entry.code : undefined,
        message: entry.message,
      }
    }
  }
  const detail = record.detail
  if (typeof detail === 'string' && detail) return { message: detail }
  if (detail && typeof detail === 'object') {
    const entry = detail as Record<string, unknown>
    if (typeof entry.message === 'string' && entry.message) {
      return { message: entry.message }
    }
    if (typeof entry.error === 'string' && entry.error) {
      return { code: entry.error, message: entry.error }
    }
  }
  return null
}

/**
 * Normalize a thrown phlo-api error into status, code, and a display message.
 *
 * When the thrown message embeds a JSON error body (the server transport
 * appends the raw body), the envelope `error.message` is extracted; otherwise
 * the original message is passed through unchanged so callers keep their
 * existing contract for non-envelope failures.
 */
export function describePhloApiError(error: unknown): PhloApiErrorInfo {
  if (!(error instanceof Error)) {
    return { message: 'Lakehouse API is unavailable' }
  }
  const match = error.message.match(/^phlo-api error: (\d{3})\s?([\s\S]*)$/)
  if (!match) return { message: error.message }
  const status = Number(match[1])
  const body = (match[2] ?? '').trim()
  if (body) {
    try {
      const parsed = readPhloApiErrorBody(JSON.parse(body))
      if (parsed) return { status, code: parsed.code, message: parsed.message }
    } catch {
      // Body is not JSON — report the text after the status.
    }
    return { status, message: body }
  }
  return { status, message: error.message }
}
