/** Keeps related controls compact and explains shared denials once, without hover. */
import * as React from 'react'
import { cn } from '@/lib/utils'

type Denial = { label: string; reason: string }
const GroupContext = React.createContext<
  ((id: string, denial?: Denial) => void) | null
>(null)

export function useGroupedActionReason(id: string, denial?: Denial) {
  const register = React.useContext(GroupContext)
  const label = denial?.label
  const reason = denial?.reason
  React.useEffect(() => {
    if (!register || !label || !reason) return
    register(id, { label, reason })
    return () => register(id)
  }, [register, id, label, reason])
  return register !== null
}

export function ActionGroup({
  children,
  className,
  controlsClassName,
}: {
  children: React.ReactNode
  className?: string
  controlsClassName?: string
}) {
  const [denials, setDenials] = React.useState<Record<string, Denial>>({})
  const register = React.useCallback((id: string, denial?: Denial) => {
    setDenials((current) => {
      const next = { ...current }
      if (denial) next[id] = denial
      else delete next[id]
      return next
    })
  }, [])
  const reasons = new Map<string, Array<string>>()
  for (const { label, reason } of Object.values(denials)) {
    const labels = reasons.get(reason) ?? []
    if (!labels.includes(label)) labels.push(label)
    reasons.set(reason, labels)
  }
  return (
    <GroupContext value={register}>
      <div className={cn('min-w-0 max-w-full', className)}>
        <div
          className={cn('flex flex-wrap items-center gap-2', controlsClassName)}
        >
          {children}
        </div>
        {reasons.size ? (
          <details className="mt-1.5 max-w-lg text-xs text-muted-foreground">
            <summary className="w-fit cursor-pointer rounded-sm py-0.5 underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
              {Object.keys(denials).length}{' '}
              {Object.keys(denials).length === 1 ? 'action' : 'actions'}{' '}
              unavailable · View reasons
            </summary>
            <ul className="mt-1 space-y-1 border-l-2 border-line pl-3 text-pretty">
              {[...reasons].map(([reason, labels]) => (
                <li key={reason}>
                  <span className="font-medium">{labels.join(', ')}:</span>{' '}
                  {reason}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </GroupContext>
  )
}
