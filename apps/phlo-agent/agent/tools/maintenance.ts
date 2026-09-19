/** Schedule-only semantic routing for deterministic maintenance findings. */
import { defineDynamic, defineTool } from 'eve/tools'
import { z } from 'zod'
import { routeMaintenanceFindings } from '../lib/maintenance-routing'
import { isScheduleAppAuth } from '../lib/trust'

const PYTHON_AUDIT_COMMAND = String.raw`python3 - <<'PY'
import json
import os
import subprocess

repo = "/workspace/repo"
uv = os.path.expanduser("~/.local/bin/uv")
lockfiles = subprocess.check_output(
    ["git", "-C", repo, "ls-files", "*uv.lock"], text=True
).splitlines()
reports = []
for lockfile in lockfiles:
    project_dir = os.path.join(repo, os.path.dirname(lockfile))
    result = subprocess.run(
        [uv, "audit", "--locked", "--project", project_dir, "--output-format", "json"],
        capture_output=True,
        text=True,
    )
    try:
        audit = json.loads(result.stdout)
        reports.append({"lockfile": lockfile, "exitCode": result.returncode, "audit": audit})
    except json.JSONDecodeError:
        reports.append({
            "lockfile": lockfile,
            "exitCode": result.returncode,
            "error": (result.stderr or result.stdout)[-1000:],
        })
print(json.dumps(reports, separators=(",", ":")))
PY`

interface AuditReport {
  lockfile: string
  exitCode: number
  audit?: {
    summary?: { vulnerabilities?: number; adverse_statuses?: number }
    vulnerabilities?: unknown[]
    adverse_statuses?: unknown[]
  }
  error?: string
}

const findingSchema = z.object({
  id: z.string().min(1).max(200),
  packageName: z.string().min(1).max(200),
  installedVersion: z.string().min(1).max(100),
  fixedVersions: z.array(z.string().min(1).max(100)).max(10),
  dependencyScope: z.enum(['runtime', 'development', 'optional', 'unknown']),
  advisorySummary: z.string().min(1).max(1_000),
  affectedManifests: z.array(z.string().min(1).max(300)).max(5),
  releaseNotes: z.string().max(1_000).optional(),
})

export default defineDynamic({
  events: {
    'turn.started': (_event, ctx) => {
      if (!isScheduleAppAuth(ctx.session.auth.current)) return null
      return {
        maintenance__audit_python_dependencies: defineTool({
          description: 'Deterministically audit every tracked uv.lock once. Call this before loading skills, searching GitHub, or running shell commands. If clean is true, stop the maintenance turn immediately without any other tool call.',
          inputSchema: z.object({}),
          async execute(_input, toolCtx) {
            const sandbox = await toolCtx.getSandbox()
            const result = await sandbox.run({
              command: `cd /workspace/repo && ${PYTHON_AUDIT_COMMAND}`,
            })
            if (result.exitCode !== 0) {
              return {
                clean: false,
                auditedLockfiles: 0,
                errors: [{ lockfile: '<audit-runner>', error: String(result.stderr).slice(-1_000) }],
              }
            }

            let reports: AuditReport[]
            try {
              reports = JSON.parse(String(result.stdout)) as AuditReport[]
            } catch {
              return {
                clean: false,
                auditedLockfiles: 0,
                errors: [{ lockfile: '<audit-output>', error: 'uv audit returned invalid aggregate JSON.' }],
              }
            }

            const vulnerabilities = reports.flatMap((report) =>
              (report.audit?.vulnerabilities ?? []).map((finding) => ({
                lockfile: report.lockfile,
                finding,
              })))
            const adverseStatuses = reports.flatMap((report) =>
              (report.audit?.adverse_statuses ?? []).map((status) => ({
                lockfile: report.lockfile,
                status,
              })))
            const errors = reports
              .filter((report) => report.audit === undefined)
              .map((report) => ({
                lockfile: report.lockfile,
                error: report.error ?? `uv audit exited ${report.exitCode} without JSON output.`,
              }))

            return {
              clean: vulnerabilities.length === 0
                && adverseStatuses.length === 0
                && errors.length === 0,
              auditedLockfiles: reports.length,
              vulnerabilityCount: vulnerabilities.length,
              adverseStatusCount: adverseStatuses.length,
              findings: vulnerabilities.slice(0, 20),
              adverseStatuses: adverseStatuses.slice(0, 20),
              errors,
              truncated: vulnerabilities.length > 20 || adverseStatuses.length > 20,
            }
          },
        }),
        maintenance__route_findings: defineTool({
          description: 'Route compact, machine-discovered dependency vulnerability evidence into bounded maintenance lanes with Jev. This tool does not select versions, modify files, waive findings, or deliver artifacts. Batch all findings from one audit into one call.',
          inputSchema: z.object({
            findings: z.array(findingSchema).min(1).max(20),
          }),
          async execute({ findings }) {
            return routeMaintenanceFindings(findings)
          },
        }),
      }
    },
  },
})
