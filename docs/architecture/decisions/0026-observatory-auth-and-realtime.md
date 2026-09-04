# 26. Observatory authentication and real-time updates

Date: 2025-12-20

## Status

Accepted

## Context

Observatory needs two production-readiness features:

1. **Authentication (phlo-h2c)**: Protect Observatory endpoints with environment-level authentication. Currently all server functions are unauthenticated.

2. **Real-time Updates (phlo-cil)**: Show live status updates for materializations and quality checks without manual page refresh.

Both are from epic `phlo-6mz: Observatory Production Readiness`.

## Decision

### 1. Authentication

Use a simple token-based approach for environment-level protection.

#### Environment Configuration

```bash
# .phlo/.env.local
OBSERVATORY_AUTH_ENABLED=true
OBSERVATORY_AUTH_TOKEN=your-secret-token-here
```

#### Auth Middleware

Create `auth.server.ts` with a middleware function that all server functions can use:

```typescript
export function requireAuth(
  request: Request,
): void | { error: string; status: 401 } {
  if (process.env.OBSERVATORY_AUTH_ENABLED !== "true") return;

  const token = request.headers.get("X-Observatory-Token");
  if (token !== process.env.OBSERVATORY_AUTH_TOKEN) {
    return { error: "Unauthorized", status: 401 };
  }
}
```

#### Client Token Storage

Add auth token to settings schema and include in all server function calls:

```typescript
// observatorySettings.ts additions
export const observatorySettingsSchema = z.object({
  // ... existing fields
  auth: z
    .object({
      token: z.string().optional(),
    })
    .optional(),
});
```

### 2. Real-time Updates

Use polling with configurable intervals. TanStack Query's `refetchInterval` provides this natively.

#### Settings Extension

```typescript
// Add to observatorySettings schema
realtime: z.object({
  enabled: z.boolean(),
  intervalMs: z.number().int().min(1000).max(60000), // 1s - 60s
}).optional();
```

Default: `{ enabled: true, intervalMs: 5000 }`

#### Polling Hook

Create `useRealtimePolling.ts`:

```typescript
export function useRealtimePolling<T>(
  queryFn: () => Promise<T>,
  queryKey: string[],
  options?: { enabled?: boolean },
) {
  const { settings } = useObservatorySettings();
  const intervalMs = settings.realtime?.intervalMs ?? 5000;
  const enabled = options?.enabled ?? settings.realtime?.enabled ?? true;

  return useQuery({
    queryKey,
    queryFn,
    refetchInterval: enabled ? intervalMs : false,
    refetchIntervalInBackground: false,
  });
}
```

#### Use Cases

1. **Dashboard health metrics** (`routes/index.tsx`) - show live health status
2. **Quality checks** (`routes/quality/index.tsx`) - live check results
3. **Asset materialization status** - show in-progress indicators

### 3. Modified Files

| File                          | Changes                                |
| ----------------------------- | -------------------------------------- |
| `server/auth.server.ts`       | [NEW] Auth middleware                  |
| `lib/observatorySettings.ts`  | Add auth and realtime settings         |
| `hooks/useRealtimePolling.ts` | [NEW] Polling hook wrapper             |
| `routes/index.tsx`            | Add polling for health metrics         |
| `routes/quality/index.tsx`    | Add polling for quality checks         |
| `routes/settings.tsx`         | Add auth token and polling interval UI |
| `server/*.server.ts`          | Add optional auth check to handlers    |

## Consequences

### Positive

- Observatory protected in production environments
- Live updates improve observability experience
- Minimal changes - uses existing patterns
- Backward compatible (auth disabled by default)

### Negative

- Token must be configured manually per environment
- Polling adds load to backend services
- No SSE/WebSocket support (future enhancement)

### Risks

- Token in localStorage is visible in browser devtools (acceptable for internal tool)

## Verification Plan

### Auth Testing

1. Set `OBSERVATORY_AUTH_ENABLED=true` and `OBSERVATORY_AUTH_TOKEN=test123`
2. Open Observatory without token - should show auth error
3. Add token to Settings - should load normally
4. Test each server function returns 401 without valid token

### Polling Testing

1. Open Dashboard and Quality pages
2. Verify "last updated" timestamps change automatically
3. Adjust polling interval in Settings - verify change takes effect
4. Disable polling - verify no automatic updates

### TypeScript/Lint

```bash
cd packages/phlo-observatory/src/phlo_observatory && npm run check && npm test
```

## Beads

- phlo-h2c: Observatory: Authentication (environment-level) (complete)
- phlo-cil: Observatory: Real-time updates (polling/SSE) (complete)
