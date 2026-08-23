/**
 * Sandbox workspace constants and run-output helpers for the agent container.
 * REPO_DIR is the fixed mount point of the user repository in the container.
 */
export const REPO_DIR = '/workspace/repo'

/** Prefer stderr: failed runs surface their diagnostics there, while stdout carries progress noise. */
export function runOutput(run: { stdout?: unknown; stderr?: unknown }): string {
  return String(run.stderr ?? '').trim() || String(run.stdout ?? '').trim()
}
