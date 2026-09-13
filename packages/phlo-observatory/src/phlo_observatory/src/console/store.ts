/**
 * Focus state for the console: which object the inspector is showing.
 * Encoded in the URL as ?focus=<kind>:<id> so a selection is a link you
 * can paste to someone else.
 */

export type FocusKind =
  | 'asset'
  | 'dataset'
  | 'table'
  | 'service'
  | 'check'
  | 'op'

export interface FocusRef {
  kind: FocusKind
  id: string
}

const KINDS = new Set<FocusKind>([
  'asset',
  'dataset',
  'table',
  'service',
  'check',
  'op',
])

export function parseFocus(raw: unknown): FocusRef | null {
  if (typeof raw !== 'string') return null
  const sep = raw.indexOf(':')
  if (sep <= 0) return null
  const kind = raw.slice(0, sep) as FocusKind
  const id = raw.slice(sep + 1)
  if (!KINDS.has(kind) || !id) return null
  return { kind, id }
}

export function formatFocus(ref: FocusRef): string {
  return `${ref.kind}:${ref.id}`
}
