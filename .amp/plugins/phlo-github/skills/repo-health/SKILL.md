---
name: repo-health
description: Audit Phlo for grounded maintenance findings and deliver a bounded GitHub issue or draft pull request. Load only from a scheduled phlo-maintenance thread.
---

# Repository health

Work from current `origin/main`. Read `CONTRIBUTING.md` and all applicable
`AGENTS.md` files before changing anything.

Inspect these areas:

1. Published documentation that contradicts current source, CLI help, schemas,
   package metadata, or tests.
2. Examples and starters that use removed APIs or no longer validate.
3. Repeated required-CI failures with a concrete repository-owned cause.
4. Missing regression coverage for an observed, reproducible defect.
5. Repository conventions that have drifted from their documented contract.

Every finding must cite the concrete file and contradictory source or executed
check. Prove absence by enumerating the relevant surface. Search open issues and
pull requests before creating anything.

Deliver at most one artifact per run:

- For a mechanical, low-risk fix: create a feature branch, make only that fix,
  run targeted checks and the relevant broader checks, then pass the exact
  `origin/main` SHA, complete changed-file contents, `agent/*` branch, title,
  and body to `publish_phlo_maintenance_pull_request`. Include all check results.
- For anything requiring product judgment: create one focused GitHub issue with
  context, evidence, expected outcome, and acceptance criteria through
  `publish_phlo_maintenance_issue`. Do not code it.

Never push with Git, merge, mark a draft ready, publish, release, alter `.github`
workflows, `.amp` automation, or secrets, or create work merely to fill the run.
If no grounded work is warranted, finish without creating an artifact.
