/**
 * Status pill with a dot colored via the data-state attribute.
 */
import type {
  ObservatoryHealthState,
  ObservatoryServiceStatus,
} from '@/observatory/api/types'
import { StatusBadge as KitStatusBadge } from '@/components/observatory/status'

type StatusValue = ObservatoryHealthState | ObservatoryServiceStatus

export function StatusBadge({
  label,
  state,
}: {
  label: string
  state: StatusValue
}) {
  return <KitStatusBadge label={label} state={state} />
}
