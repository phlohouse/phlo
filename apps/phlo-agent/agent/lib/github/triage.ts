/**
 * Triage label allowlist and validation for agent-proposed issue labels:
 * bounded set size, allowlisted values only, at most one label per category.
 */
const TYPE_LABELS = new Set(['bug', 'documentation', 'enhancement', 'question'])
const PRIORITY_LABELS = new Set(['P0', 'P1', 'P2', 'P3'])
const DOMAIN_LABELS = new Set([
  'audit',
  'correctness',
  'dead-code',
  'dependencies',
  'quality',
  'security',
  'testing',
  'tooling',
])

const TRIAGE_LABELS = new Set([
  ...TYPE_LABELS,
  ...PRIORITY_LABELS,
  ...DOMAIN_LABELS,
  'ready-for-agent',
])

/**
 * Validate an agent-proposed label set: every label must be on the triage
 * allowlist, at most four labels total, and at most one per category so a
 * triage run cannot assign conflicting types, priorities, or domains.
 */
export function issueTriageLabelsAllowed(labels: unknown): boolean {
  if (!Array.isArray(labels) || labels.length === 0 || labels.length > 4) return false
  if (!labels.every((label) => typeof label === 'string' && TRIAGE_LABELS.has(label))) {
    return false
  }
  return labels.filter((label) => TYPE_LABELS.has(label)).length <= 1
    && labels.filter((label) => PRIORITY_LABELS.has(label)).length <= 1
    && labels.filter((label) => DOMAIN_LABELS.has(label)).length <= 1
}
