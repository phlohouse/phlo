/** Prevents browsers and shared caches from retaining live Observatory responses. */
import {
  createStartHandler,
  defaultStreamHandler,
} from '@tanstack/react-start/server'
import type { RequestHandler } from '@tanstack/react-start/server'
import type { Register } from '@tanstack/react-router'
import { withServerFunctionBodyLimit } from '@/lib/request-body-limit'

const handleRequest = createStartHandler(defaultStreamHandler)

const fetch: RequestHandler<Register> = async (...args) => {
  const response = await withServerFunctionBodyLimit(args[0], (request) =>
    handleRequest(request, args[1]),
  )
  // Static files bypass this handler and retain their immutable cache policy.
  const headers = new Headers(response.headers)
  headers.set('Cache-Control', 'private, no-store')
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  })
}

export default { fetch }
