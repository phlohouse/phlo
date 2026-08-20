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

export function issueTriageLabelsAllowed(labels: unknown): boolean {
  if (!Array.isArray(labels) || labels.length === 0 || labels.length > 4) return false
  if (!labels.every((label) => typeof label === 'string' && TRIAGE_LABELS.has(label))) {
    return false
  }
  return labels.filter((label) => TYPE_LABELS.has(label)).length <= 1
    && labels.filter((label) => PRIORITY_LABELS.has(label)).length <= 1
    && labels.filter((label) => DOMAIN_LABELS.has(label)).length <= 1
}
