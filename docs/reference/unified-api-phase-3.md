# Phase-3 `/api/v1` assets and overview slice

This is a partial backend delivery for [issue #1035, phase 3](https://github.com/phlohouse/phlo/issues/1035). It is not the Observatory UI wiring and does not claim phase-3 acceptance.

Every route requires an authenticated principal, an allow decision from the configured policy backend, and exactly one `env=prod|staging` selector. The server resolves that environment through `PHLO_V1_ENVIRONMENTS`; Dagster assets are filtered by the mapped repository location before data is returned. The Nessie ref remains operator-controlled and is not accepted from callers. Authorization uses `asset.read` for asset, source, layer, and overview reads, and `run.read` for materialization history. Failures do not become empty asset lists.

## Routes

| Method and path | Evidence returned |
| --- | --- |
| `GET /api/v1/assets?env=...&limit=...&cursor=...` | Dagster asset key, description, compute kind, group, source flag, dependencies, and latest materialization timestamp/run ID. When the same key is defined in multiple locations and Dagster history cannot be disambiguated, `history_scoped` is false and history fields are null. Cursor is bounded to `env` and this list; limit is 1–500. |
| `GET /api/v1/assets/{asset_id}?env=...` | Asset definition and Dagster `TableSchemaMetadataEntry` columns, when present. `schema_observed_at` is set only when the latest materialization carries schema metadata; static definition metadata has no fabricated observation time. |
| `GET /api/v1/assets/{asset_id}/runs?env=...&limit=...` | Bounded asset materialization history (timestamp, run ID, step key), not a complete audit trail of Dagster runs. |
| `GET /api/v1/sources?env=...&limit=...&cursor=...` | The same asset records filtered by Dagster's explicit `isSource` flag, before pagination. |
| `GET /api/v1/layers?env=...&limit=...&cursor=...` | Counts and latest materialization grouped by Dagster's real `groupName`; it does not assert that Dagster groups are governance/data layers. |
| `GET /api/v1/overview?env=...` | Asset and materialized-asset counts, latest materialization timestamp, persisted phase-2 incident counts, and freshness counts. Freshness is `fresh`/`stale` only where an explicit positive per-asset SLA exists and a materialization timestamp is available; other assets count as `unknown`. Audit counts are null because this route has no aggregate audit source. |

Asset definitions and materialization evidence come from Dagster GraphQL; incident counts and explicit freshness SLA overrides come from the phase-2 durable incident store. Dagster outages, malformed responses, and incident-store outages fail the overview instead of returning synthetic zeroes. This slice does not query Nessie data contents; the selected Nessie ref is deliberately not represented as proof of a ref-bound snapshot.

## Not implemented; source boundary

- **Ref-aware bounded data preview:** not available. The existing table preview reads the configured local/provider path and is not bound to a request-selected Nessie ref. Reusing it would allow cross-environment reads. No v1 preview is mounted until a read-only query/catalog provider can bind the allowlisted ref and enforce row/byte/time bounds.
- **Materialization estimate and latest/backfill/full actions:** not available in v1. Existing Dagster actions use the active process provider and do not guarantee that the selected Nessie ref is honored. No cost source exists, so no estimate is fabricated. The phase-2 operation journal is local SQLite and does not provide #989's cross-replica exclusion; this phase does not add mutations.
- **Audit/check history or audit creation:** not available. The asset materialization event log is not a substitute for a quality/audit check result. Phase 2 only has incident signals for real Dagster asset-check failures; aggregate audit records outside those events have no unified environment-scoped query or creation owner in this API slice.
- **Schema history and snapshot history:** not available. Current Dagster schema metadata is returned when present, but no stable versioned schema-history or Nessie snapshot-history source has been identified.
- **Column lineage:** not available in this route. Dagster dependency keys are returned as asset-level dependencies; column lineage metadata has no agreed typed contract here.
- **Phase-3 UI and complete overview freshness/run/audit/incident panels:** separate client work. `/overview` currently includes real incident counts and SLA-derived freshness counts, but does not claim complete audit or all-run evidence.

No migration or shared/production write is part of this change.
