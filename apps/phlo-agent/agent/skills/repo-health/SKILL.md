---
name: repo-health
description: Audit Phlo for grounded maintenance findings and deliver bounded GitHub issues or draft pull requests. Load when the repository-health schedule fires.
---

# Repository health

Work from current `origin/main` in `/workspace/repo`. Read `CONTRIBUTING.md` and
all applicable `AGENTS.md` files before changing anything.

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

Deliver at most two artifacts per run:

- For a mechanical, low-risk fix: create a feature branch, make only that fix,
  run targeted checks and the relevant broader checks, commit it, call
  `git__push`, then open a **draft** pull request. Include all check results.
- For anything requiring product judgment: create one focused GitHub issue with
  context, evidence, expected outcome, and acceptance criteria. Do not code it.

Never merge, mark a draft ready, publish, release, alter workflows or secrets,
or create work merely to fill the run. If no grounded work is warranted, finish
without creating an artifact.
