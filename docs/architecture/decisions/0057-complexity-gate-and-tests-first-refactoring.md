# ADR 0057: Cyclomatic complexity gate with a shrinking baseline

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: September 2026, completed October 2026
- Evidence: issue [#1001](https://github.com/phlohouse/phlo/issues/1001) and
  [#1033](https://github.com/phlohouse/phlo/pull/1033) (merged 2026-09-25);
  issue [#996](https://github.com/phlohouse/phlo/issues/996) and
  [#1106](https://github.com/phlohouse/phlo/pull/1106) (merged 2026-10-11)
- Decision owner: Phlo project maintainers

## Context

Before September 2026 Phlo had no complexity threshold. Ruff's `select` did
not include `C90`, and the Observatory ESLint configuration had no `complexity`
rule. The 2026-09-24 audit (#996, #1001) measured 78 Python production
functions above 15 with Ruff's McCabe count, 209 with radon or py-cyclo (which
also count `and`/`or`), and 55 Observatory functions above 15 with ESLint. The
most frequently changed hotspots included `start_cmd`, `_load_dataset_profile`,
`wap_auto_promotion_sensor` and `ComposeGenerator._build_service_config`.
Several offenders had less than 50% line coverage.

Issue #1001 chose Ruff's McCabe check because it runs in the existing
`ruff check` lane without a new dependency. radon and py-cyclo remained the
reporting tools for refactor pull requests.

## Decision

1. **Threshold 15.** New Python and Observatory TypeScript functions must have
   cyclomatic complexity 15 or below: `[tool.ruff.lint.mccabe] max-complexity = 15`
   in the root [pyproject.toml](../../../pyproject.toml), and
   `complexity: ['error', 15]` in the Observatory ESLint configuration.
2. **Explicit, shrinking baseline.** Existing Python offenders carry
   `noqa: C901` on their definitions. A refactor removes its marker. The
   baseline only shrinks.
3. **Tests are exempt.** `tests/**` and `packages/*/tests/**` ignore `C901`;
   test names describe scenarios.
4. **One gate, run from the root configuration.** Packages have their own Ruff
   settings, so `make check`, pre-commit and CI run
   `ruff check --config pyproject.toml --select C901 .` explicitly.
5. **Test first, then simplify.** When a function's line coverage is below 80%,
   the refactor pull request adds behavioural tests for the moved branches before
   the refactor commit. Refactors extract pure decision functions before I/O
   helpers, keep behaviour and contract tests unchanged, keep coverage the same
   or higher, and include a before-and-after complexity table (#996).

## Consequences

- #1106 split the 13 assigned large offenders into phases. On `origin/main`
  `7feced4d` three Python `noqa: C901` markers remain, down from 75 when
  #1033 landed.
- The Observatory suppression file from #1033 was deleted when the legacy UI was
  replaced ([#1051](https://github.com/phlohouse/phlo/pull/1051)). The replacement
  application passes the rule without a baseline.
  [CONTRIBUTING.md](../../../CONTRIBUTING.md) still describes the suppression file.
- The gate does not prove that complex code is well tested. #996 states that its
  closure does not claim every production function is below the threshold.
- `apps/phlo-github-writer` has no ESLint configuration and is outside the gate.

## Alternatives Considered

- **radon or py-cyclo as the gate:** rejected for gating because they add a
  dependency and count boolean operators differently; retained for reporting.
- **Refactoring without a baseline:** rejected; the baseline let the gate land
  immediately while existing offenders were scheduled.
- **Refactoring low-coverage code directly:** rejected in #996; coverage below
  80% requires tests first.

## Related

- [CONTRIBUTING.md](../../../CONTRIBUTING.md): complexity guidance for contributors.
- [ADR 0056](0056-core-layering-and-acyclic-imports.md): the other structural gate in `make check`.
