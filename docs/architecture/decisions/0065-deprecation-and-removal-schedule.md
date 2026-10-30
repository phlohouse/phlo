# ADR 0065: Deprecations name a removal release, a replacement and a usage measure

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: September–October 2026
- Evidence: issue [#860](https://github.com/phlohouse/phlo/issues/860) and
  [#899](https://github.com/phlohouse/phlo/pull/899) (merged 2026-09-04,
  approved deprecation pass); issue [#997](https://github.com/phlohouse/phlo/issues/997)
  and [#1082](https://github.com/phlohouse/phlo/pull/1082) (merged 2026-10-09,
  removal deadlines and usage counters)
- Decision owner: Phlo project maintainers

## Context

The September 2026 programme (#836, #859, #860) narrowed the supported surface.
Issue #860 set the rule that every surface approved for removal gets exactly one
pass: an announced deprecation with a migration path, then a versioned removal,
with no stragglers. It also set the converse: a surface without an approved
decision stays, however unused it looks. #899 shipped the warnings and migration
guidance.

Issue #997 then found that deprecation warnings said only "an upcoming release",
that `backfill`, `schedule` and `transform` decorators were accepted but never
executed, that a callable-module shim kept `phlo.ingestion(...)` working, and that
`phlo.__all__` was built from sets, so its order could change between runs.
Nothing measured whether anyone still used the deprecated paths.

## Decision

1. **Every deprecation names a removal release and a replacement.** Warnings,
   the [Python API removal schedule](../../reference/python-api.md#removal-schedule)
   and the changelog state the version. Tests audit deprecation sources and public
   compatibility APIs for a named release (`tests/plugins/test_deprecation_release.py`).
2. **The current deadline is 0.19.0.** Deprecated ingestion aliases,
   `phlo_quality`, the callable `phlo.ingestion` module shim, the sync and async
   operation adapters, legacy `TransformationPlugin`, Dagster
   `IngestionEnginePlugin`, the `PHLO_REGULATED_MODE` and `PHLO_MCP_TRACE_FILE`
   controls, legacy Dagster env files and the Observatory Dataset-production
   aliases are removed in 0.19.0.
3. **Dormant APIs fail loudly.** Declarations that would never execute raise an
   error with the replacement instead of registering silent no-ops.
4. **Usage is counted, not assumed.** Each compatibility path increments a
   `phlo.legacy.*.uses` metric through canonical telemetry. A release owner may
   remove a path early only after confirming zero use across enabled deployment
   telemetry. Disabled telemetry does not prove zero use, and a zero server-side
   count does not prove that remote clients ignore a response field.
5. **Codemods outlive the API they migrate.** `phlo migrate decorators-2026-05`
   remains through at least 0.20.0, one release after the APIs it rewrites are
   removed.
6. **Public exports are deterministic.** `phlo.__all__` is a stable, sorted
   sequence.

## Consequences

- Users get at least one release of warnings, a named replacement and, where
  possible, a codemod before anything is removed.
- The 0.19.0 release must remove every path on the schedule or move its deadline
  explicitly; a removal release cannot pass silently.
- Persisted identifiers, such as run-evidence record names and existing URL
  contracts, are not renamed through this process. They need a schema version
  or an explicit retention decision
  ([public name compatibility](../../reference/public-name-compatibility.md)).

## Alternatives Considered

- **Deprecating without a version:** the state #997 found; rejected because
  users cannot plan a migration.
- **Removing unused-looking surfaces without a decision:** rejected by #860.
- **Keeping dormant decorators as no-ops:** rejected; users could adopt APIs that
  did nothing.

## Related

- [ADR 0006](0006-public-api-and-structured-logging.md): explicit public exports.
- [ADR 0052](0052-domain-vocabulary.md): the vocabulary changes that produced the Dataset-production aliases.
- [Process settings](../../contributing/process-settings.md): removal releases for legacy environment controls.
