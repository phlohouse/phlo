# Evidence, audit, and compliance

Run evidence, operational audit events, and compliance evidence packs answer different questions about a pipeline. After reading this page, you can choose the right record, storage boundary, and verification command for an operational or compliance investigation.

## Three records

Run evidence records a pipeline run, its events, and its report. The store can be SQLite for local use or PostgreSQL for durable reports. The supported durable report boundary is described in [Durable run-report support boundary](../reference/packages.md#durable-run-report-support-boundary).

The operational audit log uses `CanonicalAuditEvent` and writes JSONL records to `.phlo/audit/operations.jsonl`. Inspect it with `phlo audit query` or `phlo audit tail`, using `--operation`, `--since`, `--limit`, or `--json` as needed.

Phlo signs each audit record with HMAC-SHA256 and chains it to the previous record, so a removed or altered record is detectable. `phlo compliance export-evidence` creates a ZIP containing audit records, signatures, and manifest data. `phlo compliance verify-evidence` checks the external HMAC over the canonical checksums.

Break-glass review and activity monitoring are available to API and plugin integrations. There is no CLI command for them.

## Regulated mode

Set `PHLO_REGULATED` to enable regulated mode. `PHLO_REGULATED_MODE` remains a deprecated alias.

Production readiness checks provide the preflight result used by the production guidance. `phlo doctor` diagnoses local setup and service health, while `phlo support status` checks the bundled support contract.

## Where evidence appears

Dagster run records are the primary execution record. The Observatory service and API expose durable per-run reports. MCP provides read-only resources and tools for project and runtime inspection through `phlo mcp`.

## Retention and storage

Run evidence storage selection and report support depend on the configured provider and the support boundary in [Package reference](../reference/packages.md#durable-run-report-support-boundary). Compliance exports are files written to the path supplied to `--output`, and the HMAC key comes from `PHLO_EVIDENCE_HMAC_KEY` or `PHLO_AUDIT_HMAC_KEY`.

## Where to look next

Use [Collect evidence](../guides/collect-evidence.md) for an operator workflow and [Auth and access](../reference/auth-and-access.md) for identities, scopes, and audit controls.
