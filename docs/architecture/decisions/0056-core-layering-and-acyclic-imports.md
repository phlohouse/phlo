# ADR 0056: Core layering and acyclic imports enforced by import-linter

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: October 2026
- Evidence: issue [#1019](https://github.com/phlohouse/phlo/issues/1019)
  (audit parent [#980](https://github.com/phlohouse/phlo/issues/980)) and
  [#1096](https://github.com/phlohouse/phlo/pull/1096) (merged 2026-10-11)
- Decision owner: Phlo project maintainers

## Context

[ADR 0043](0043-core-package-restructuring.md) proposed a layered directory
layout for `src/phlo` but did not define enforceable dependency rules. The
September 2026 audit (#1019) found that core had no module-level cycles, but
counting function-local imports it had one 59-module strongly connected
component spanning `phlo`, `phlo.capabilities`, `phlo.flow`, helpers, plugin
discovery, hooks, infrastructure, governance, security and telemetry. There was
also a cycle between `phlo.rbac.compiler` and `phlo.rbac.compilers`, and small
cycles in `phlo-iceberg` (catalog and CLI utilities, resource and tables) and
`phlo-dagster` (the two CLI log modules). Package roots said their lazy imports
existed "to avoid circular imports". Deferred imports prevented import-time
failure but kept the coupling and made initialisation order hard to follow.
No import-linter configuration existed.

When #1096 was measured against current `main`, the graph had five strongly
connected components, including a 64-module core component.

## Decision

Enforce the layering with six import-linter contracts in the root
[pyproject.toml](../../../pyproject.toml), together with a complete AST cycle check:

1. **No cycles** (`acyclic_siblings`) among `phlo.*` subpackages, `phlo_iceberg`
   and `phlo_dagster`.
2. **Capability definitions are inner.** `phlo.capabilities.*` must not import
   `phlo.plugins`, `phlo.helpers`, `phlo.infrastructure`, `phlo.application`,
   `phlo.cli` or `phlo.logging`.
3. **Aggregators re-export outward only.** No module under `phlo` imports the
   `phlo`, `phlo.capabilities` or `phlo.helpers` package roots; code imports the
   defining module instead.
4. **The CLI is the outer layer.** Application, capability, helper, infrastructure,
   plugin, security, configuration, hook, operation, migration, run-evidence,
   logging and telemetry modules must not import `phlo.cli`.
5. **Application, capability and domain layers**, from outer to inner:
   `phlo.__main__`/`phlo.cli`; application composition (`application`,
   `infrastructure`, `helpers`, `plugins`, `logging`); capability resolvers and
   adapters; `phlo.capabilities.registry`; `phlo.capabilities.specs`; domain
   contracts (capability interfaces, migration specs, run-evidence contracts and
   models, security adapters, Dataset, hook events); and foundational
   configuration, exceptions and logging context.
6. **RBAC layering** inside `phlo.rbac`: `sync`, `registry`, `compilers`,
   `compiler`, `config`, `models`.

`scripts/check_imports.py` runs `lint-imports --no-cache` and then
`scripts/check_import_cycles.py`, which builds the graph from every explicit AST
import, including deferred, relative and `TYPE_CHECKING` imports. Both run in
`make check` and CI. The contracts enforce from the start: there is no
report-only mode and no ignored import.

## Consequences

- Moving a module across layers, or adding an inward import, fails `make check`.
  The fix is to move code or invert the dependency, not to defer the import.
- Package roots (`phlo`, `phlo.capabilities`, `phlo.helpers`) remain public
  re-export surfaces for users but are never dependencies inside core.
- #1096 kept existing public re-exports and provider contract identities
  compatible, and kept `_compose_base_cmd` at its old CLI path for
  compatibility. Fresh-process regressions cover both import orders.
- The AST cycle check does not see computed dynamic imports or implicit parent
  package initialisation.

## Alternatives Considered

- **Deferred imports to break cycles:** the previous practice; rejected because
  the coupling remained.
- **Starting contracts in report-only mode:** proposed in #1019 as a migration
  step. #1096 removed the violations first and landed enforcing contracts directly.
- **Module-level-only cycle detection:** rejected because it reported no cycles
  while the deferred-import component existed.

## Related

- [ADR 0043](0043-core-package-restructuring.md): proposed core restructuring.
- [ADR 0055](0055-provider-neutral-core-boundary.md): the boundary between core and providers.
- [scripts/check_imports.py](../../../scripts/check_imports.py), [scripts/check_import_cycles.py](../../../scripts/check_import_cycles.py).
