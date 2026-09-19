/**
 * Same-origin API proxy for the production server.
 *
 * In development the Vite server proxies `/api/observatory` to phlo-api; the
 * built application has no such middleware, so this server route forwards
 * requests to `PHLO_API_URL` inside the deployment network. Cookies,
 * Authorization and the observatory CSRF header pass through unchanged, so
 * browser sessions keep working same-origin without the browser ever needing
 * provider URLs or credentials.
 */
import { createFileRoute } from "@tanstack/react-router";

const HOP_BY_HOP = new Set([
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function upstreamBase(): string {
  return process.env.PHLO_API_URL ?? "http://localhost:4000";
}

async function proxyToPhloApi(request: Request): Promise<Response> {
  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, upstreamBase());

  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP) {
    headers.delete(name);
  }

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Node's fetch requires duplex when forwarding a request body stream.
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
    });
  } catch (error) {
    return Response.json(
      {
        detail: `phlo-api unreachable at ${upstreamBase()}`,
        reason: error instanceof Error ? error.message : String(error),
      },
      { status: 503 },
    );
  }

  const responseHeaders = new Headers(upstream.headers);
  for (const name of HOP_BY_HOP) {
    responseHeaders.delete(name);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const Route = createFileRoute("/api/observatory/$")({
  server: {
    handlers: {
      GET: ({ request }) => proxyToPhloApi(request),
      POST: ({ request }) => proxyToPhloApi(request),
      PUT: ({ request }) => proxyToPhloApi(request),
      PATCH: ({ request }) => proxyToPhloApi(request),
      DELETE: ({ request }) => proxyToPhloApi(request),
      OPTIONS: ({ request }) => proxyToPhloApi(request),
      HEAD: ({ request }) => proxyToPhloApi(request),
    },
  },
});
