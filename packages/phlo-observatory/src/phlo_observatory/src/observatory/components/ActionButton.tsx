/**
 * Run button for a single observatory action. High and critical risk actions
 * show a warning icon; the button stays disabled until the action is enabled.
 */
import { AlertTriangle, Play } from 'lucide-react'

import type { ObservatoryAction } from '@/observatory/api/types'

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
    <button
      aria-label={action.label}
      className="phlo-observatory-action-button"
      disabled={!action.enabled}
      onClick={() => onRun(action.id)}
      title={title}
      type="button"
    >
      <Icon className="size-4" aria-hidden="true" />
      <span>{action.label}</span>
    </button>
  )
}
