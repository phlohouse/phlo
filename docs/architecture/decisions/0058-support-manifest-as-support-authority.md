# ADR 0058: Support manifest as the authority for support scope, maturity and gates

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: July 2026, extended September 2026
- Evidence: [#579](https://github.com/phlohouse/phlo/pull/579) (merged 2026-07-13,
  "declare v1 support boundary"); [#895](https://github.com/phlohouse/phlo/pull/895)
  (merged 2026-09-04, supported-surface narrowing and version single-sourcing for
  issue [#859](https://github.com/phlohouse/phlo/issues/859))
- Decision owner: Phlo project maintainers

## Context

Before July 2026 support claims were spread across the plugin registry, package
READMEs, user documentation and `default: true` flags in service manifests. These
sources disagreed. The registry's `verified` flag was read as a support claim, and
the root of the problem was recorded in issue #837 as preview providers inheriting
blessed-profile support. Version numbers were maintained by hand in up to four
places per package.

[#579](https://github.com/phlohouse/phlo/pull/579) added a versioned
`phlo.support.v1` schema and a machine-readable manifest with an offline
validator. Issue #837 later proposed a separate support-decision governance
record; it was closed as deferred, with the comment that no separate support
governance model would be introduced. The manifest therefore remained the only
authority.

## Decision

1. **One authority.** [registry/support/v1.json](../../../registry/support/v1.json),
   validated against `registry/support/schema/v1.json`, is the single source of
   truth for which packages, services and capabilities Phlo supports, their
   current maturity, applicable release gates, runtime claims, production
   status and exclusions. `src/phlo/support_data/v1.json` is a byte-identical
   wheel copy read offline by the `phlo` CLI.
2. **Target support is separate from maturity and readiness.** Each component
   records `scope`, `target_status` and `current_maturity` separately. Gates
   (`security`, `run_evidence`, `maintenance`, `upgrade_restore`,
   `golden_path`) record what blocks each component. A target profile is not a
   production-readiness claim.
3. **Four profiles.** `blessed_core` (scope-frozen), `supported_optional`
   (target capabilities only), `preview` (available without a v1 guarantee) and
   `development_only` (local or test use). The validator pins the approved
   profiles and required v1 capabilities.
4. **The blessed core stack.** `blessed_core` contains `phlo`, `phlo-api`,
   `phlo-core-plugins`, `phlo-dagster`, `phlo-dbt`, `phlo-dlt`, `phlo-iceberg`,
   `phlo-minio`, `phlo-nessie`, `phlo-observatory`, `phlo-observe-plugin`,
   `phlo-pandera`, `phlo-postgres` and `phlo-trino`, and their services: Iceberg
   tables on MinIO, a Nessie catalogue backed by PostgreSQL, Trino for queries,
   Dagster for orchestration, DLT, dbt and Pandera for ingestion, transformation
   and quality, and the API and Observatory. The `phlo[defaults]` extra installs
   exactly these packages. Polaris, RustFS, Delta, ClickHouse and the other
   alternatives are `preview`. MinIO is the default object store when the choice
   would otherwise be ambiguous; RustFS stays preview (#895).
5. **Evidence binds claims to the repository.** Every claim lists evidence paths.
   `scripts/validate_support_manifest.py` checks that paths and Markdown anchors
   exist, that named claims bind to specific source and tests, that profile
   membership and gates derive consistently, and that providers declare the
   current core compatibility range ([ADR 0054](0054-workspace-monorepo-and-release-sets.md)).
6. **Release set pinning.** The manifest's `release_set` pins exact blessed-core
   package versions, image references and the configuration and database schema
   versions. ReleaseX replacements update these values in each release PR.
7. **Other surfaces derive from the manifest.** The support matrix and package
   reference pages are generated from it, and `scripts/check_version_drift.py`
   rejects disagreement between package metadata, both registry copies and the
   manifest.

## Consequences

- A support claim changes only through a reviewed manifest change that passes the
  validator. Prose cannot widen support.
- `default: true` in a `service.yaml` selects a service for new projects; it does
  not establish support (see [Service topology](../service-topology.md)).
- At `origin/main` `7feced4d` every blessed-core component is `alpha` with
  blocked gates, and `production.status` is `blocked`. Gate promotion is a
  separate decision ([ADR 0050](0050-freeze-release-promotion-contract.md),
  section 7).
- The manifest records runtime claims: Python 3.12, Linux on the CI-tested path,
  and Docker Compose v2 ([ADR 0059](0059-runtime-and-deployment-baseline.md)).

## Alternatives Considered

- **Registry `verified` flags or README wording as support claims:** rejected in
  #579 because they disagreed and could not be validated.
- **A separate support-decision governance record (#837):** closed as deferred;
  the manifest and its validator carry the decisions.
- **Treating `default: true` as support:** rejected; selection and support are
  different facts.

## Related

- [ADR 0047](0047-v1-production-trust-and-readiness.md): the production requirements recorded in the manifest.
- [ADR 0050](0050-freeze-release-promotion-contract.md): release promotion and support-gate separation.
- [Support matrix](../../reference/support-matrix.md) (generated).
- [Engineering map](../../contributing/engineering-map.md): manifest ownership and the wheel copy.
