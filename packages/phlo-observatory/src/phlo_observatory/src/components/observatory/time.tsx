/**
 * Time formatting helpers: relative labels for recent timestamps and a
 * compact absolute fallback.
 */
const relativeFormatter = new Intl.RelativeTimeFormat('en', {
  numeric: 'auto',
})

export function formatRelativeTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const deltaMs = date.getTime() - Date.now()
  const abs = Math.abs(deltaMs)
  const minutes = deltaMs / 60_000
  if (abs < 60_000) return 'just now'
  if (abs < 3_600_000)
    return relativeFormatter.format(Math.round(minutes), 'minute')
  const hours = minutes / 60
  if (abs < 86_400_000)
    return relativeFormatter.format(Math.round(hours), 'hour')
  const days = hours / 24
  if (abs < 604_800_000)
    return relativeFormatter.format(Math.round(days), 'day')
  return formatDateTime(value)
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    day: '2-digit',
    hour: '2-digit',
    hour12: false,
    minute: '2-digit',
    month: 'short',
  })
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}
