function isBatch(kind?: string): boolean {
  return kind?.includes('schedule') ?? false
}

function environment(): string {
  return process.env.VERCEL_ENV ?? process.env.NODE_ENV ?? 'development'
}

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
