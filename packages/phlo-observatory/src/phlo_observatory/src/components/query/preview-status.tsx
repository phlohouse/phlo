/** Summarizes connection evidence without claiming deployment attestation. */
import * as React from 'react'
import { useRouter } from '@tanstack/react-router'
import type { z } from 'zod'
import type { readinessSchema } from '@/lib/data/api/readiness'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type PreviewCheck = z.infer<
  typeof readinessSchema
>['capabilities'][string][number]

export function QueryPreviewStatus({
  checks,
}: {
  checks: Array<PreviewCheck>
}) {
  const router = useRouter()
  const [checking, setChecking] = React.useState(false)
  const blockers = checks.filter((check) =>
    ['unavailable', 'degraded'].includes(check.state),
  )
  const disabled = checks.some((check) => check.state === 'disabled')
  const connected = checks.some(
    (check) => check.reason_code === 'backend_reachable',
  )
  const ready =
    checks.length > 0 && checks.every((check) => check.state === 'ready')
  const attention = blockers.length > 0 || checks.length === 0
  const title = previewTitle({ attention, disabled, ready, connected })
  const messages = Array.from(
    new Set(
      (attention
        ? blockers
        : disabled
          ? checks.filter((check) => check.state === 'disabled')
          : []
      )
        .flatMap((check) => [check.message, check.remediation])
        .filter(Boolean),
    ),
  )

  return (
    <div
      role="status"
      className={cn(
        'shrink-0 border-b px-4 py-2 text-xs',
        attention
          ? 'border-warn bg-warn-soft'
          : 'border-line bg-sunken text-muted-foreground',
      )}
    >
      <details>
        <summary className="cursor-pointer font-medium text-foreground">
          {title}
        </summary>
        <div className="grid gap-2 pt-2">
          {attention || disabled ? (
            messages.length ? (
              messages.map((message) => <p key={message}>{message}</p>)
            ) : (
              <p>Preview status could not be verified. Check again to retry.</p>
            )
          ) : ready ? (
            <p>
              The backend reports that its query preview readiness checks
              passed.
            </p>
          ) : (
            <>
              <p>
                {connected
                  ? 'Passed: authenticated connection to the query backend.'
                  : 'Preview is configured. The authenticated backend connection has not been verified.'}
              </p>
              <p>
                Deployment safeguards are not verified by this status: read-only
                access, query limits, restart recovery and credential renewal.
              </p>
              <p>
                This is not a query error. You can use a connected preview for
                local development. Verify these safeguards before sharing a
                deployment; local development does not disable them.
              </p>
            </>
          )}
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            disabled={checking}
            onClick={async () => {
              setChecking(true)
              try {
                await router.invalidate()
              } finally {
                setChecking(false)
              }
            }}
          >
            {checking ? 'Checking…' : 'Check again'}
          </Button>
        </div>
      </details>
    </div>
  )
}

function previewTitle({
  attention,
  disabled,
  ready,
  connected,
}: {
  attention: boolean
  disabled: boolean
  ready: boolean
  connected: boolean
}) {
  if (attention) return 'Query preview unavailable'
  if (disabled) return 'Query preview disabled'
  if (ready) return 'Query preview ready'
  return connected ? 'Query preview connected' : 'Query preview configured'
}
