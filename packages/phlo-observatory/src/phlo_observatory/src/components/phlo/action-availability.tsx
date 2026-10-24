/** Shares deployment preflight and caches resource-specific checks per actor/environment. */
import * as React from 'react'
import type {
  ActionAvailability,
  ActionContext,
} from '@/lib/data/api/action-availability'
import { getActionAvailability } from '@/lib/data/api/action-availability'

const unavailable = {
  allowed: false,
  reason: 'Action availability could not be verified. Refresh to check again.',
}
const ActionAvailabilityContext = React.createContext<{
  initial: ActionAvailability | null
  load: (context: ActionContext) => Promise<ActionAvailability>
} | null>(null)

export function ActionAvailabilityProvider({
  env,
  initial,
  children,
}: {
  env: string
  initial: ActionAvailability | null
  children: React.ReactNode
}) {
  const value = React.useMemo(() => {
    const requests = new Map<string, Promise<ActionAvailability>>()
    return {
      initial,
      load(context: ActionContext) {
        const key = JSON.stringify(context)
        let request = requests.get(key)
        if (!request) {
          request = getActionAvailability({ data: { env, ...context } })
          requests.set(key, request)
        }
        return request
      },
    }
  }, [env, initial])
  return (
    <ActionAvailabilityContext value={value}>
      {children}
    </ActionAvailabilityContext>
  )
}

export function useActionPermission(
  operation?: string,
  context?: ActionContext,
) {
  const provider = React.useContext(ActionAvailabilityContext)
  const key = JSON.stringify(context)
  const [result, setResult] = React.useState<{
    key: string
    page: ActionAvailability | null
  }>()
  React.useEffect(() => {
    if (!provider || !key || !operation) return
    let active = true
    const parsed: ActionContext = JSON.parse(key)
    provider.load(parsed).then(
      (page) => {
        if (active) setResult({ key, page })
      },
      () => {
        if (active) setResult({ key, page: null })
      },
    )
    return () => {
      active = false
    }
  }, [provider, key, operation])
  if (!provider || !operation) return { allowed: true, reason: null }
  const page = context
    ? result?.key === key
      ? result.page
      : null
    : provider.initial
  return page?.actions[operation] ?? unavailable
}
