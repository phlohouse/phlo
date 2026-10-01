/** Checks frontend liveness independently of API credentials and connectivity. */
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/healthz')({
  server: {
    handlers: {
      GET: () => Response.json({ status: 'ok' }),
      HEAD: () => new Response(null, { status: 200 }),
    },
  },
})
