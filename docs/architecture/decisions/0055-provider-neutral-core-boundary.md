# ADR 0055: Provider-neutral core and declared package edges

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: August–September 2026
- Evidence: issue [#639](https://github.com/phlohouse/phlo/issues/639) and
  [#709](https://github.com/phlohouse/phlo/pull/709) (2026-08-19); issue
  [#994](https://github.com/phlohouse/phlo/issues/994) and
  [#1036](https://github.com/phlohouse/phlo/pull/1036) (2026-09-25)
- Decision owner: Phlo project maintainers

## Context

[ADR 0030](0030-unified-plugin-system-with-registry.md),
[ADR 0033](0033-hook-based-capability-plugins.md) and
[ADR 0041](0041-capability-primitives-and-orchestrator-adapters.md) introduced
entry-point discovery, hooks and capability specs. None of them states the rule
that makes those mechanisms necessary: the core distribution must not depend on
any particular provider.

In July 2026 that rule did not hold. Issue #639 (audit finding ARCH-04) recorded
that workflow validation and schema discovery in `src/phlo/cli` imported Pandera,
and namespace resolution imported DLT, bypassing the capability selection used
elsewhere. Issue #994 then found that the existing boundary test checked only
top-level imports, so imports nested in functions escaped it. Cross-package
imports between providers were also undeclared in package metadata.

## Decision

1. **Core never imports a provider package.** No module under `src/phlo` may
   import any `phlo_*` package shipped from `packages/*`, at module scope,
   inside a function or under `TYPE_CHECKING`.
2. **Core reaches providers through neutral contracts.** Providers register
   implementations through `phlo.plugins.*` entry points and the capability
   registry. When no provider is installed, core returns a stable
   capability-unavailable result instead of failing on import
   ([#709](https://github.com/phlohouse/phlo/pull/709)).
3. **The root distribution does not pull provider runtime stacks.** Dagster,
   FastAPI, pandas, Pandera, database drivers and libcst ship as the `runtime`
   and `codemods` extras, not as core dependencies.
4. **Provider-to-provider imports are declared and reviewed.** A cross-package
   import must be backed by a declared dependency or extra, and must appear in
   the reviewed allow-list of provider edges
   ([#1036](https://github.com/phlohouse/phlo/pull/1036)). New edges need
   explicit review.

The rules are enforced by AST tests that include nested imports:
`tests/cli/test_cli_provider_neutral.py`, `tests/test_dependency_boundaries.py`
and `tests/plugins/test_package_dependency_declarations.py`. A further check in
`tests/dataset/test_boundaries.py` keeps the Dataset core provider-free.

## Consequences

- Removing a provider package leaves core importable; affected commands report
  an unavailable capability.
- New provider behaviour starts with a neutral spec in `phlo.capabilities` and an
  adapter in the provider package, not a conditional import in core.
- Provider docstrings may show provider imports as examples. The AST tests ignore
  docstrings, so these examples do not break the rule.
- Some optional integrations are resolved dynamically by name (for example
  `phlo.telemetry` loads `phlo_observe`, which is an external SDK, not a workspace
  provider). Dynamic imports are outside the AST tests and need review.

## Alternatives Considered

- **Import providers lazily from core:** this was the state before #639. It avoids
  import-time failure but keeps the coupling and hides it from top-level scans.
- **Top-level-only boundary checks:** replaced in #1036 because nested imports
  escaped them.
- **Unrestricted provider-to-provider imports:** rejected in #1036; each existing
  edge is listed and new edges need review.

## Related

- [ADR 0030](0030-unified-plugin-system-with-registry.md), [ADR 0033](0033-hook-based-capability-plugins.md),
  [ADR 0041](0041-capability-primitives-and-orchestrator-adapters.md): the discovery mechanisms this rule relies on.
- [ADR 0048](0048-blessed-run-evidence-composition.md): provider evidence contributions under the same rule.
- [ADR 0056](0056-core-layering-and-acyclic-imports.md): layering inside core.
- [Capabilities and plugins reference](../../reference/capabilities-and-plugins.md).
