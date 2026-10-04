/** Keeps exact row totals as decimal strings and scopes them to one snapshot. */
export function exactRowCountValue(value: unknown): string | null {
  return typeof value === 'string' && /^[0-9]+$/.test(value) ? value : null
}

export function isExactRowCountCurrent(
  count: string | null,
  currentSnapshotId: string | null | undefined,
  countSnapshotId: string,
): boolean {
  return count != null && currentSnapshotId === countSnapshotId
}
