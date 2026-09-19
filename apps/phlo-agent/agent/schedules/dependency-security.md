---
cron: "0 2 * * *"
---

Run Phlo's focused dependency-security pass against current main. Load the
upstream-sync skill, but perform only its Python vulnerability procedure. Audit
every tracked `uv.lock`, matching `.github/workflows/security.yml`, and search
existing issues, Renovate work, and pull requests before proposing anything.
Use `maintenance__route_findings` once for all fixable findings. If a routine,
mechanical remediation has no existing Renovate pull request, create at most
one verified draft pull request using uv's machine-reported fixed versions and
normal lock/constraint tooling; Jev must not choose the version. Route changes
that require compatibility or security judgment according to the skill. If
autonomous writes are disabled, complete the audit without trying to deliver
an artifact. Create nothing when all audits pass or existing work already
owns every finding. Never merge, waive a finding, publish, or release.
