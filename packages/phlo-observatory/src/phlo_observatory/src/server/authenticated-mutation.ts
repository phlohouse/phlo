/**
 * Shared inbound mutation-authorization middleware for Observatory server
 * functions.
 *
 * Plan 003B transport seam: every guarded mutation server function extracts
 * the signed-in human's accepted credential (bearer JWT per ADR 0047) from the
 * inbound request, sanitizes it, and forwards it to phlo-api. The credential
 * exists only for the lifetime of the server request: it is never returned to
 * the client, attached to query/cache keys, or exposed to React components.
 *
 * Anything that is not a well-formed Bearer credential is dropped so the API
 * fails closed (401). A cross-site mutation request (hostile Origin) is
 * rejected before the handler. Bearer auth is not cookie-based, so a CSRF
 * token is not applicable to this path; the API never trusts cookie-only
 * authentication for mutations.
 */

import { createMiddleware } from '@tanstack/react-start'

export function mutationBearerAuthorization(
  value: string | null,
): string | undefined {
  if (value === null) return undefined
  return /^Bearer\s+\S+$/i.test(value) ? value : undefined
}

export function trustedSameOrigin(request: Request): boolean {
  const origin = request.headers.get('origin')
  if (origin === null) {
    // Non-browser callers (no ambient credentials) are not a CSRF vector.
    return true
  }
  const host = request.headers.get('host')
  if (host === null) return false
  try {
    return new URL(origin).host === host
  } catch {
    return false
  }
}

export const mutationAuthorization = createMiddleware({
  type: 'request',
}).server(({ next, request }) => {
  if (!trustedSameOrigin(request)) {
    throw new Error('Cross-site mutation request rejected')
  }
  return next({
    context: {
      authorization: mutationBearerAuthorization(
        request.headers.get('authorization'),
      ),
    },
  })
})
