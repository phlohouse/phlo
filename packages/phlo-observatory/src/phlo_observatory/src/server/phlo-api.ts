/**
 * Phlo API Server Client
 *
 * Helper for server functions to call the Python phlo-api backend.
 * Handles URL resolution for both Docker and local dev environments.
 */

const PHLO_API_URL = process.env.PHLO_API_URL || 'http://localhost:4000'

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE'

interface RequestOptions {
  authorization?: string
  method?: HttpMethod
  params?: Record<string, string | number | boolean | undefined>
  body?: unknown
  timeoutMs?: number
}

/**
 * Internal request handler - all HTTP methods go through here.
 *
 * Any non-2xx response throws an Error whose message carries both the status
 * code and the response body, so callers can classify failures without a
 * second round trip. Requests abort after timeoutMs (30s unless overridden).
 * Only apiGet forwards an Authorization header to the backend today.
 */
async function request<T>(
  endpoint: string,
  options: RequestOptions = {},
): Promise<T> {
  const {
    authorization,
    method = 'GET',
    params,
    body,
    timeoutMs = 30000,
  } = options

  const url = new URL(`${PHLO_API_URL}${endpoint}`)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) {
        url.searchParams.set(key, String(value))
      }
    }
  }

  const headers = new Headers()
  const hasHeaders = Boolean(authorization) || body !== undefined
  if (authorization !== undefined) {
    // Never forward a header-injection vector. A credential containing a
    // line break is malformed, not "credential-like"; reject it outright.
    if (/[\r\n]/.test(authorization)) {
      throw new Error('Malformed authorization header rejected')
    }
    headers.set('Authorization', authorization)
  }
  if (body !== undefined) headers.set('Content-Type', 'application/json')

  const response = await fetch(url.toString(), {
    method,
    headers: hasHeaders ? headers : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(timeoutMs),
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(`phlo-api error: ${response.status} ${text}`)
  }

  return response.json()
}

/**
 * Make a GET request to phlo-api
 */
export async function apiGet<T>(
  endpoint: string,
  params?: Record<string, string | number | boolean | undefined>,
  timeoutMs = 30000,
  authorization?: string,
): Promise<T> {
  return request<T>(endpoint, { authorization, params, timeoutMs })
}

/**
 * Make a POST request to phlo-api
 */
export async function apiPost<T>(
  endpoint: string,
  body?: unknown,
  timeoutMs = 30000,
  authorization?: string,
): Promise<T> {
  return request<T>(endpoint, {
    method: 'POST',
    body,
    timeoutMs,
    authorization,
  })
}

/**
 * Make a PUT request to phlo-api
 */
export async function apiPut<T>(
  endpoint: string,
  body?: unknown,
  timeoutMs = 30000,
  authorization?: string,
): Promise<T> {
  return request<T>(endpoint, { method: 'PUT', body, timeoutMs, authorization })
}
