/**
 * Docker label helpers for compose-scoped filtering.
 */

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function getComposeLabelValue(
  labels: string | undefined,
  key: string,
): string | null {
  if (!labels) {
    return null
  }
  const pattern = new RegExp(`(?:^|,)${escapeRegExp(key)}=([^,]+)(?:,|$)`)
  const match = labels.match(pattern)
  return match ? match[1] : null
}

export function matchesComposeProject(
  labels: string | undefined,
  composeProject: string | null,
): boolean {
  // A null composeProject disables filtering: every container matches, so
  // callers outside a compose deployment still see the full container list.
  if (!composeProject) {
    return true
  }
  return (
    getComposeLabelValue(labels, 'com.docker.compose.project') ===
    composeProject
  )
}
