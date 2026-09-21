---
name: upstream-sync
description: Check Phlo's upstream ecosystem for compatibility-impacting releases and publish a bounded draft PR or issue. Load only from a scheduled phlo-maintenance thread.
---

# Upstream sync

Monitor the upstream projects that Phlo integrates, not only version numbers.
No shell or code execution is available. Use Librarian to build the inventory
from current `phlohouse/phlo` main on every run by enumerating:

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
it. Read `docs/operations/release-management.md`, the latest public main-branch
security CI result, and authoritative upstream advisories before proposing
dependency work. When no existing work owns a vulnerability, create a bounded
issue for remediation.

Treat the repository's `.github/workflows/security.yml` and its latest public
main-branch result as the authoritative executed Python vulnerability inventory.
Keep version availability, manifest discovery, and test selection deterministic:

- A routine update first reuses any existing dependency remediation pull
  request. If none exists, create an issue naming the affected manifests and
  normal constraint, lock, and validation commands for an implementation agent.
- Compatibility work requires mapping the change to every affected Phlo
  surface and naming its compatibility checks in the issue acceptance criteria.
- Security judgment, ambiguous impact, low-confidence classification, and
  findings without a fix may produce a focused issue, but never a waiver or
  speculative patch.

The audit's machine-readable fix data and uv's resolver remain authoritative.
Never reinterpret uncertainty as permission to act.

Search existing issues and pull requests first. Ignore version churn with no
demonstrable effect on Phlo.

- If a compatibility adaptation is a documentation-only correction whose
  complete output follows directly from inspected source, it may be sent to
  `publish_phlo_maintenance_pull_request` with the exact main SHA and complete
  changed-file contents. State that CI must validate the **draft** pull request.
- If an update changes code, locks, generated files, tests, or requires a product
  or security decision, call
  `publish_phlo_maintenance_issue` with one focused issue explaining the
  upstream release, affected Phlo surfaces, evidence, risk, migration path, and
  decision.
- If nothing warrants action, create nothing.

Never push with Git, merge, publish, release, alter `.github` workflows, `.amp`
automation, or secrets, or open more than one artifact per run.
