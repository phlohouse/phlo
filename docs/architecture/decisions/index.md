# Architecture Decision Records

Architecture Decision Records (ADRs) freeze the contracts Phlo depends on:
identity, authority, state, and compatibility rules that code and plans must
follow once a decision is accepted.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-quality-check-contract.md) | 1. Standardize quality check naming and metadata contract | Accepted |
| [0002](0002-pandas-datetime-coercion-scope.md) | 2. Limit datetime coercion during Pandera validation | Accepted |
| [0003](0003-quality-severity-policy.md) | 3. Define a severity policy for quality checks (blocking vs warn) | Accepted |
| [0004](0004-partition-scoped-checks-and-samples.md) | 4. Make checks partition-aware and include failure sampling | Accepted |
| [0005](0005-dbt-translator-metadata.md) | 5. Store dbt compiled SQL in Dagster asset metadata (not descriptions) | Accepted |
| [0006](0006-public-api-and-structured-logging.md) | 6. Maintain explicit public exports and avoid print() in library code | Accepted |
| [0007](0007-cli-services-architecture.md) | 7. Refactor `phlo services` into testable, composable units | Accepted |
| [0008](0008-scaffolds-without-placeholders.md) | 8. Scaffold generators must emit working code (no TODO placeholders by default) | Accepted |
| [0009](0009-publishing-yaml-scaffold.md) | 9. Scaffold `publishing.yaml` from dbt manifest with an idempotent merge strategy | Accepted |
| [0010](0010-emit-dagster-asset-checks.md) | 10. Emit Pandera and dbt results as Dagster asset checks | Accepted |
| [0011](0011-observatory-quality-from-dagster.md) | 11. Observatory Quality Center sources from Dagster GraphQL with caching and drilldown | Accepted |
| [0012](0012-observatory-data-explorer-branch-aware-routing.md) | 12. Make Observatory Data Explorer branch-aware via path-based routing | Accepted |
| [0013](0013-cli-generate-pandera-from-dlt-inference.md) | 13. Generate Pandera schemas from DLT inference via CLI sample runs | Accepted |
| [0014](0014-observatory-ui-redesign-with-shadcn-lyra-preset.md) | 14. Redesign Observatory UI with shadcn Lyra preset (fixed design system) | Accepted |
| [0015](0015-observatory-table-engine-on-tanstack.md) | 15. Standardize Observatory tables on TanStack Table (+ virtualization) | Accepted |
| [0016](0016-observatory-settings-and-query-guardrails.md) | 16. Add Observatory settings and server-side query guardrails | Accepted |
| [0017](0017-observatory-contributing-rows-inline-pagination-and-sampling.md) | 17. Add inline contributing rows with pagination and deterministic sampling | Accepted |
| [0018](0018-observatory-command-palette.md) | 18. Observatory command palette global search | Accepted |
| [0019](0019-observatory-metadata-caching.md) | 19. Observatory metadata caching | Accepted |
| [0020](0020-observatory-table-browser-improvements.md) | 20. Observatory table browser improvements | Accepted |
| [0021](0021-observatory-stage-diff-view.md) | 21. Observatory stage diff view | Accepted |
| [0022](0022-ingestion-validation-hardening.md) | 22. Ingestion validation hardening & cleanup | Accepted |
| [0023](0023-observatory-saved-queries-and-bookmarks.md) | 23. Observatory saved queries and bookmarks | Accepted |
| [0024](0024-github-quality-reconciliation-checks.md) | 24. GitHub Example: phlo_quality Reconciliation Checks | Accepted |
| [0025](0025-observatory-responsive-layout.md) | 25. Observatory responsive layout | Accepted |
| [0026](0026-observatory-auth-and-realtime.md) | 26. Observatory authentication and real-time updates | Accepted |
| [0027](0027-observatory-performance-monitoring.md) | 27. Observatory performance monitoring and budgets | Accepted |
| [0028](0028-unified-logging-and-observability.md) | 28. Unified Logging and Observability | Accepted |
| [0029](0029-cli-services-enhancements.md) | 29. CLI Services Enhancements: Restart Command and Profile Flag Fix | Accepted |
| [0030](0030-unified-plugin-system-with-registry.md) | ADR 0030: Unified Plugin System with Registry | Accepted |
| [0031](0031-observatory-as-core-and-dx-improvements.md) | ADR 0031: Observatory as Core with Plugin DX Improvements | Accepted |
| [0032](0032-eliminate-docker-images-port-observatory-to-python.md) | ADR 0032: Eliminate Docker Images - Port Observatory Server to Python | Accepted |
| [0033](0033-hook-based-capability-plugins.md) | ADR 0033: Hook-Based Capability Plugins | Accepted |
| [0034](0034-migrate-to-ty-typechecker.md) | ADR 0034: Migrate to TY Type Checker | Accepted |
| [0035](0035-package-integration-tests.md) | ADR 0035: Package-Level Integration Tests | Accepted |
| [0036](0036-iceberg-maintenance-observability.md) | ADR 0036: Iceberg Maintenance Observability | Accepted |
| [0037](0037-advanced-reconciliation-checks.md) | ADR 0037: Advanced Reconciliation Checks | Accepted |
| [0038](0038-golden-path-e2e-workflow-test.md) | ADR 0038: Golden Path E2E Workflow Test | Accepted |
| [0039](0039-dbt-project-under-workflows.md) | ADR 0039: dbt Project Under Workflows | Accepted |
| [0040](0040-centralized-logging-layer.md) | ADR 0040: Centralized Logging Layer and Log Routing | Accepted |
| [0041](0041-capability-primitives-and-orchestrator-adapters.md) | ADR 0041: Capability Primitives and Orchestrator Adapters | Accepted |
| [0042](0042-observatory-extension-manifests-and-native-ui-plugins.md) | ADR 0042: Observatory Extension Manifests and Native UI Plugins | Accepted |
| [0043](0043-core-package-restructuring.md) | ADR 0043: Core Package Restructuring | Accepted |
| [0044](0044-cli-command-ownership-by-package.md) | ADR 0044: CLI Command Ownership by Package | Accepted |
| [0045](0045-package-settings-and-cli-extraction.md) | ADR 0045: Package-Owned Settings and CLI Extraction | Accepted |
| [0046](0046-phlo-contracts-for-schema-migration-scaffolding.md) | ADR 0046: Phlo Contracts for Table-Store-Native Migration Scaffolding | Accepted |
| [0047](0047-v1-production-trust-and-readiness.md) | ADR 0047: V1 Production Trust and Readiness Contract | Accepted |
| [0048](0048-blessed-run-evidence-composition.md) | ADR 0048: Blessed Run-Evidence Composition | Accepted |
| [0049](0049-v1-continuity-and-upgrade-contract.md) | ADR 0049: V1 Continuity and Upgrade Contract | Accepted |
| [0050](0050-freeze-release-promotion-contract.md) | ADR 0050: Freeze the Release Promotion Contract | Accepted |
| [0051](0051-dataset-authority.md) | ADR 0051: Dataset authority contract | Accepted |
