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

export function issueTriageLabelsAllowed(labels: unknown): labels is string[] {
  if (!Array.isArray(labels) || labels.length > 4) return false
  if (!labels.every((label) => typeof label === 'string' && TRIAGE_LABELS.has(label))) return false
  return labels.filter((label) => TYPE_LABELS.has(label)).length <= 1
    && labels.filter((label) => PRIORITY_LABELS.has(label)).length <= 1
    && labels.filter((label) => DOMAIN_LABELS.has(label)).length <= 1
}

export function validBranch(branch: unknown): branch is string {
  return typeof branch === 'string'
    && /^agent\/[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?$/.test(branch)
    && !branch.includes('..')
    && !branch.includes('//')
}

export function validChangedPath(path: unknown): path is string {
  return typeof path === 'string'
    && path.length > 0
    && path.length <= 300
    && !path.startsWith('/')
    && !path.startsWith('.git/')
    && !path.startsWith('.github/')
    && !path.startsWith('.amp/')
    && !path.split('/').includes('..')
}
