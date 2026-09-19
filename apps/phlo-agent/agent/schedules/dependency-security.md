---
cron: "0 2 * * *"
---

Run Phlo's focused dependency-security pass against current main. Your first
action must be exactly one call to `maintenance__audit_python_dependencies`.
Do not load a skill, search GitHub, inspect audit caches or commits, or run any
shell command before that call. If it returns `clean: true`, stop immediately:
make no other tool call, create nothing, and conclude the turn.

Only when that deterministic audit returns findings or an error, load the
upstream-sync skill and follow its Python vulnerability procedure. Search
existing issues and bot- or human-authored pull requests before proposing
anything. Use `maintenance__route_findings` once for all fixable findings. If a
routine, mechanical remediation has no existing pull request, create at most
one verified draft pull request using uv's machine-reported fixed versions and
normal lock/constraint tooling; Jev must not choose the version. Route changes
that require compatibility or security judgment according to the skill. If
autonomous writes are disabled, complete the audit without trying to deliver
an artifact. Never merge, waive a finding, publish, or release.
