export const REPO_DIR = '/workspace/repo'

export function runOutput(run: { stdout?: unknown; stderr?: unknown }): string {
  return String(run.stderr ?? '').trim() || String(run.stdout ?? '').trim()
}
