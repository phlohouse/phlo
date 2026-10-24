/** Caps server-function bodies before TanStack parses JSON or multipart files. */
export const SERVER_FUNCTION_BODY_LIMIT = 12 * 1024 * 1024

function tooLarge() {
  return new Response('Server function request exceeds the 12 MiB limit.', {
    status: 413,
    headers: { 'Cache-Control': 'private, no-store' },
  })
}

export async function withServerFunctionBodyLimit(
  request: Request,
  handler: (request: Request) => Response | Promise<Response>,
  limit = SERVER_FUNCTION_BODY_LIMIT,
): Promise<Response> {
  if (
    request.method !== 'POST' ||
    !new URL(request.url).pathname.startsWith('/_serverFn/')
  )
    return handler(request)
  const declared = request.headers.get('content-length')
  if (declared && Number(declared) > limit) return tooLarge()
  // h3 lazily constructs a native Request when these metadata fields are read.
  // Capture them before locking its source stream with pipeThrough.
  const url = request.url
  const init: RequestInit & { duplex: 'half' } = {
    method: request.method,
    headers: new Headers(request.headers),
    signal: request.signal,
    credentials: request.credentials,
    cache: request.cache,
    redirect: request.redirect,
    referrer: request.referrer,
    referrerPolicy: request.referrerPolicy,
    integrity: request.integrity,
    keepalive: request.keepalive,
    mode: request.mode === 'navigate' ? 'same-origin' : request.mode,
    duplex: 'half',
  }
  const body = request.body
  if (!body) return handler(request)
  let bytes = 0
  let exceeded = false
  const stream = body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        bytes += chunk.byteLength
        if (bytes > limit) {
          exceeded = true
          controller.error(
            new Error('Server function request body is too large.'),
          )
          return
        }
        controller.enqueue(chunk)
      },
    }),
  )
  init.body = stream
  // Framework request proxies expose public fields but cannot satisfy native
  // Request private-field checks. Construct from the URL and explicit options.
  const bounded = new Request(url, init)
  try {
    const response = await handler(bounded)
    if (exceeded) {
      await response.body?.cancel()
      return tooLarge()
    }
    return response
  } catch (error) {
    if (exceeded) return tooLarge()
    throw error
  }
}
