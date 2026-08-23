/**
 * Status pill with a dot colored via the data-state attribute.
 */
import type {
  ObservatoryHealthState,
  ObservatoryServiceStatus,
} from '@/observatory/api/types'

type StatusValue = ObservatoryHealthState | ObservatoryServiceStatus

export function StatusBadge({
  label,
  state,
}: {
  label: string
  state: StatusValue
}) {
  return (
    <span className="phlo-observatory-pill">
      <span className="phlo-observatory-dot" data-state={state} />
      {label}
    </span>
  )
}
