/**
 * AI gateway provider options: batch traffic sorts by cost, interactive
 * traffic by time to first token, with per-environment usage tags.
 */
function isBatch(kind?: string): boolean {
  return kind?.includes('schedule') ?? false
}

function environment(): string {
  return process.env.VERCEL_ENV ?? process.env.NODE_ENV ?? 'development'
}

/**
 * Provider options for the AI gateway. Batch traffic (scheduled runs) is
 * sorted by cost because latency does not matter there; interactive traffic is
 * sorted by time to first token. Tags make usage attributable per environment
 * and channel surface.
 */
export function gatewayOptions(kind?: string) {
  return {
    providerOptions: {
      gateway: {
        caching: 'auto' as const,
        sort: isBatch(kind) ? 'cost' as const : 'ttft' as const,
        tags: [
          `phlo-agent:env:${environment()}`,
          `phlo-agent:surface:${kind ?? 'unknown'}`,
        ],
      },
    },
  }
}
