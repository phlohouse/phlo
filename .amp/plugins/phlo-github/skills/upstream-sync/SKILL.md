---
name: upstream-sync
description: Check Phlo's upstream ecosystem for compatibility-impacting releases and publish a bounded draft PR or issue. Load only from a scheduled phlo-maintenance thread.
---

# Upstream sync

Monitor the upstream projects that Phlo integrates, not only version numbers.
Do not use a hard-coded package allowlist. Build the inventory from current
`main` on every run by enumerating:

- the root `pyproject.toml`, `uv.lock`, and every `packages/*/pyproject.toml`
- every provider's packaged YAML and other service definitions for image refs
- `packages/phlo-observatory/src/phlo_observatory/package.json` and lockfile
- dependency-bearing GitHub workflows and repository configuration
- `registry/support/v1.json` for support tier, evidence, and runtime boundaries

This inventory covers every Phlo package and its external Python, JavaScript,
service, image, API, and protocol dependencies. Do not silently skip a package
because it was absent from a prior prompt. Prioritize v1-target surfaces first,
then preview surfaces, then development-only integrations, using the current
support manifest rather than model memory.

Read authoritative release notes, migration guides, and changelogs. Map removed
APIs, changed defaults, deprecations, security notices, image behavior, and
support-window changes to the exact Phlo adapters, services, tests, docs, and
version constraints they affect. When one upstream is shared by multiple Phlo
packages, identify every affected consumer before proposing work.

Search all open pull requests for existing dependency work and do not duplicate
it. Read `docs/operations/release-management.md` and the output of
`make dependency-refresh` before proposing dependency work. When no existing
work owns a vulnerability, this skill owns producing the bounded remediation.

Audit every tracked `uv.lock` with `uv audit --locked --project <lockfile-dir>
--output-format json`, matching `.github/workflows/security.yml`, as the
authoritative Python vulnerability inventory. Keep version availability,
manifest discovery, and test selection deterministic:

- A routine update first reuses any existing dependency remediation pull
  request. If none exists, a scheduled run may create one verified draft pull
  request using the audit's listed fixed versions and normal uv constraint/lock
  tooling.
- Compatibility work requires mapping the change to every affected Phlo
  surface and running its compatibility checks.
- Security judgment, ambiguous impact, low-confidence classification, and
  findings without a fix may produce a focused issue, but never a waiver or
  speculative patch.

The audit's machine-readable fix data and uv's resolver remain authoritative.
Never reinterpret uncertainty as permission to act.

Search existing issues and pull requests first. Ignore version churn with no
demonstrable effect on Phlo.

- If a compatibility adaptation is mechanical and safe against current or
  explicitly approved pins, update the code and tests, run the affected package
  checks plus dependency-refresh validation, then call
  `publish_phlo_maintenance_pull_request` with the exact `origin/main` SHA and
  complete changed-file contents. It creates the `agent/*` branch and **draft**
  pull request.
- If an update requires a product or security decision, call
  `publish_phlo_maintenance_issue` with one focused issue explaining the
  upstream release, affected Phlo surfaces, evidence, risk, migration path, and
  decision.
- If nothing warrants action, create nothing.

Never push with Git, merge, publish, release, alter `.github` workflows, `.amp`
automation, or secrets, or open more than one artifact per run.
