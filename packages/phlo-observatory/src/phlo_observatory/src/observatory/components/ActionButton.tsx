/**
 * Run button for a single observatory action. High and critical risk actions
 * show a warning icon; the button stays disabled until the action is enabled.
 */
import { AlertTriangle, Play } from 'lucide-react'

import type { ObservatoryAction } from '@/observatory/api/types'
import { Button } from '@/components/ui/button'

export function ActionButton({
  action,
  onRun,
}: {
  action: ObservatoryAction
  onRun: (actionId: string) => void
}) {
  const Icon =
    action.risk_level === 'high' || action.risk_level === 'critical'
      ? AlertTriangle
      : Play
  const title = action.reason ?? action.equivalent_cli_command ?? action.label

  return (
    <Button
      aria-label={action.label}
      disabled={!action.enabled}
      onClick={() => onRun(action.id)}
      size="sm"
      title={title}
      type="button"
      variant={
        action.risk_level === 'high' || action.risk_level === 'critical'
          ? 'outline'
          : 'default'
      }
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {action.label}
    </Button>
  )
}
