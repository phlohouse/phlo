---
name: phlo-auditing-tests
description: "Audits Phlo tests for observable contracts, meaningful regression coverage, and duplication. Use when writing or reviewing tests, consolidating suites, or removing low-value tests and test-only production hooks."
---

# Audit tests

Apply the [testing standards](../../../CONTRIBUTING.md#testing-standards)
to the changed tests or named subsystem. Use the
[engineering map](../../../docs/contributing/engineering-map.md) to identify
the implementation and the tests that own its contract. A request to audit
tests produces findings; edit only when the task authorises changes.

## 1. Establish what each test protects

Read the complete test, fixtures, implementation, callers, and relevant history.
For each new test or audit candidate, record:

- The observable behaviour or independent contract it protects.
- A plausible regression that would make its assertion fail.
- Why existing coverage does not already catch that regression.
- The public boundary through which it observes the result.

Prefer an existing case or parameter table when it can cover the missing risk.
Another layer earns a separate test only for a distinct risk, such as transport,
persistence, discovery, or lifecycle behaviour. Finish when every candidate has
a named contract or an evidence-backed reason that it adds no protection.

## 2. Challenge the assertion and fixture

Check expected values independently of the implementation. Choose asymmetric
inputs and boundary cases where a plausible wrong implementation differs from
the correct one. Confirm that negative cases fail at the intended guard rather
than an earlier fixture error.

Apply CONTRIBUTING.md's rules for parsed artifacts, mocks, isolation, and shared
contracts. In particular, inspect self-comparisons, source-string assertions,
copied inventories, private call-shape expectations, and mocks that supply the
very result being asserted. For cross-package authorization contracts, reuse
`packages/phlo-testing/src/phlo_testing/authorization_surface.py`.

Trace persistence claims to a real write and subsequent read. Check that fakes
do not manufacture the receipts, ordering, or success being tested. For bug
regressions, demonstrate failure on the unchanged code and success after the
fix for the intended reason. Report unavailable baseline proof explicitly.

## 3. Preserve distinct protection before deleting

For each proposed deletion or consolidation, name the retained test and explain
how it covers the same regression. Preserve distinct security, migration,
package, configuration, platform, and release contracts even when they are slow
or structurally similar. A failing baseline may reveal a product bug; failure
alone is not a reason to delete a test.

Inspect exports, flags, wrappers, and injection hooks used only by tests. Move
the test to the real public boundary where practical. Remove a production hook
only after verifying that no production or supported plugin consumer needs it.
Use [plugin impact assessment](../phlo-assessing-plugin-impact/SKILL.md) for
public-contract changes. Keep cleanup scoped to the audited owner, without
test-count or line-deletion targets.

## 4. Validate and report retained coverage

Run the owning suite and relevant sibling contracts using the
[verification matrix](../../../docs/contributing/verification-matrix.md).
After bootstrap, preview broader checks with
`python3 scripts/check_changed.py --dry-run`. For behaviour-preserving cleanup,
the retained contract tests must pass unchanged. Keep unavailable service or
platform checks visible as gaps rather than weakening assertions or markers.

Report findings by file and contract, the retained coverage for every removal,
regression evidence, executed checks, and unresolved risks. Separate production
changes from test changes. Finish when each candidate has a justified outcome
and the remaining suite still checks the claimed behaviours.
