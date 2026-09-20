# Evidence, audit, and compliance

Phlo keeps separate records for run evidence, operational audit events, and compliance evidence packs. The records have different storage and verification boundaries.

## Three records

Run evidence uses `PipelineRun`, `RunEvent`, and `RunReport` from `src/phlo/run_evidence`. The store can be SQLite for local use or PostgreSQL for durable reports. The supported durable report boundary is described in [Durable run-report support boundary](../reference/packages.md#durable-run-report-support-boundary).

The operational audit log uses `CanonicalAuditEvent` and writes JSONL records to `.phlo/audit/operations.jsonl`. Inspect it with `phlo audit query` or `phlo audit tail`, using `--operation`, `--since`, `--limit`, or `--json` as needed.

Compliance evidence uses `TamperEvidentAuditSink`, `EvidencePack`, and HMAC-SHA256. `phlo compliance export-evidence` creates a ZIP containing audit records, signatures, and manifest data. `phlo compliance verify-evidence` checks the external HMAC over the canonical checksums.

Break-glass review and activity monitoring are implemented by `BreakGlassManager` and `ActivityMonitor` in `src/phlo/compliance/governance`. The code defines pending, approved, denied, expired, and revoked break-glass states, but no separate CLI command is registered for these classes.

## Regulated mode

`is_regulated_mode_enabled` and its current `is_regulated` implementation are in `src/phlo/security/mode.py`. The current environment setting is `PHLO_REGULATED`. `PHLO_REGULATED_MODE` remains a deprecated alias.

`ProductionReadinessReport` and `run_production_readiness` in `src/phlo/security/production_preflight.py` provide the production preflight result used by the production guidance. `phlo doctor` diagnoses local setup and service health, while `phlo support status` checks the bundled support contract.

## Where evidence appears

Dagster run records are the primary execution record. Observatory and the API can expose durable per-run reports when the supported services and extensions are enabled. MCP provides read-only resources and tools for project and runtime inspection through `phlo mcp`.

## Retention and storage

Run evidence storage selection and report support depend on the configured provider and the support boundary in [Package reference](../reference/packages.md#durable-run-report-support-boundary). Compliance exports are files written to the path supplied to `--output`, and the HMAC key comes from `PHLO_EVIDENCE_HMAC_KEY` or `PHLO_AUDIT_HMAC_KEY`.

## Where to look next

Use [Collect evidence](../guides/collect-evidence.md) for an operator workflow and [Auth and access](../reference/auth-and-access.md) for identities, scopes, and audit controls.
