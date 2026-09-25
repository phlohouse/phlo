/**
 * Authentication middleware for Observatory server functions
 *
 * ADR: 0026-observatory-auth-and-realtime.md
 * Bead: phlo-h2c
 *
 * Uses TanStack Start's createMiddleware to provide reusable auth
 * that can be chained with any server function.
 *
 * Usage:
 * ```ts
 * import { authMiddleware } from '@/observatory/api/auth'
 *
 * export const getAssets = createServerFn()
 *   .middleware([authMiddleware])
 *   .handler(async ({ context }) => {
 *     // context.isAuthenticated is available
 *     // Auth already validated if OBSERVATORY_AUTH_ENABLED=true
 *   })
 * ```
 */

import { createMiddleware } from '@tanstack/react-start'

/**
 * PHLO_ENVIRONMENT spellings that require HTTP authorization. Mirrors
 * requires_http_authorization() in src/phlo/security/mode.py (ADR 0047
 * decision 2): prod, production, staging, and regulated are production-like;
 * development, test, blank, and absent values stay opt-in.
 */
const PRODUCTION_AUTH_ENVIRONMENTS = new Set([
  'prod',
  'production',
  'staging',
  'regulated',
])
const TRUTHY_ENV_VALUES = new Set(['1', 'true', 'yes', 'on'])

/**
 * Return whether the deployment environment is production-like, using the
 * same predicate as requires_http_authorization() on the API side: a truthy
 * PHLO_REGULATED, or PHLO_ENVIRONMENT in PRODUCTION_AUTH_ENVIRONMENTS.
 */
export function isProductionLikeEnvironment(): boolean {
  const regulated = (process.env.PHLO_REGULATED ?? '').trim().toLowerCase()
  if (TRUTHY_ENV_VALUES.has(regulated)) {
    return true
  }
  const environment = (process.env.PHLO_ENVIRONMENT ?? '').trim().toLowerCase()
  return PRODUCTION_AUTH_ENVIRONMENTS.has(environment)
}

/**
 * Check if authentication is enabled
 */
export function isAuthEnabled(): boolean {
  return (
    process.env.OBSERVATORY_AUTH_ENABLED === 'true' ||
    isProductionLikeEnvironment()
  )
}

/**
 * Get the expected auth token from environment
 */
function getExpectedToken(): string | undefined {
  return process.env.OBSERVATORY_AUTH_TOKEN
}

/**
 * Fail startup when a production-like environment provides no credential.
 *
 * Authentication is mandatory (not opt-in) in production-like environments,
 * so a missing OBSERVATORY_AUTH_TOKEN is a fatal misconfiguration rather
 * than a silent anonymous control plane.
 */
export function assertObservatoryAuthConfiguration(): void {
  if (!isProductionLikeEnvironment() || getExpectedToken()) {
    return
  }
  throw new Error(
    'Observatory refuses to start: PHLO_ENVIRONMENT is production-like ' +
      '(prod, production, staging or regulated) or PHLO_REGULATED is enabled, ' +
      'but OBSERVATORY_AUTH_TOKEN is not set. Configure ' +
      'OBSERVATORY_AUTH_TOKEN to authenticate service-lifecycle and ' +
      'operational calls, or unset PHLO_ENVIRONMENT and PHLO_REGULATED for ' +
      'local development.',
  )
}

/**
 * Auth error result type
 */
export interface AuthError {
  error: string
  status: 401
}

/**
 * Validate auth token
 *
 * Returns undefined if auth passes, or an AuthError if it fails.
 * When auth is disabled, always returns undefined (passes).
 */
function validateAuth(token?: string): AuthError | undefined {
  if (!isAuthEnabled()) {
    return undefined
  }

  const expectedToken = getExpectedToken()

  if (!expectedToken) {
    console.warn(
      '[auth] OBSERVATORY_AUTH_ENABLED=true but OBSERVATORY_AUTH_TOKEN is not set',
    )
    return { error: 'Authentication misconfigured', status: 401 }
  }

  if (!token) {
    return { error: 'Authentication required', status: 401 }
  }

  if (token !== expectedToken) {
    return { error: 'Invalid authentication token', status: 401 }
  }

  return undefined
}

/**
 * Auth middleware for server functions
 *
 * Add to any server function with .middleware([authMiddleware])
 * Validates authToken from input when OBSERVATORY_AUTH_ENABLED=true or the
 * deployment environment is production-like.
 */
export const authMiddleware = createMiddleware({ type: 'function' })
  .inputValidator((input: { authToken?: string }) => input)
  .server(async ({ next, data }) => {
    const authError = validateAuth(data.authToken)

    if (authError) {
      // Throw error to stop execution chain
      throw new Error(authError.error)
    }

    return next({
      context: {
        isAuthenticated: isAuthEnabled(),
      },
    })
  })
