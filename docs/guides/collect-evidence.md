# Collect and verify evidence

Use this guide when you need durable run evidence, operational audit records, and a tamper-evident compliance evidence pack.

## Before you start

Install `phlo` and `phlo-postgres` for durable run evidence. Set `PHLO_RUN_EVIDENCE_DB_URL` to the PostgreSQL DSN used by the Dagster webserver and daemon. For compliance exports, set `PHLO_EVIDENCE_HMAC_KEY` or `PHLO_AUDIT_HMAC_KEY` outside the project files.

## 1. Select the run-evidence store

Set the PostgreSQL connection in the project environment.

```bash
export PHLO_RUN_EVIDENCE_DB_URL=postgresql://<user>:<password>@<host>:<port>/<database>
```

`default_run_evidence_store()` selects `PostgresRunEvidenceStore` when `PHLO_RUN_EVIDENCE_DB_URL` is set. Without it, local runs use `SQLiteRunEvidenceStore` at `.phlo/run-evidence.sqlite`. The production support boundary for durable reports is documented in [Package reference](../reference/packages.md#durable-run-report-support-boundary).

## 2. Read the operational audit log

Query or tail the JSONL operation records.

```bash
phlo audit query --since 1h
phlo audit tail --limit 20
```

The records are written to `.phlo/audit/operations.jsonl`. Add `--operation` to filter a query, or add `--json` when another tool consumes the result.

## 3. Export an evidence pack

Set the HMAC key before exporting an archive.

```bash
export PHLO_EVIDENCE_HMAC_KEY=<key-material>
```

Before exporting, confirm that the output path is new or that replacing it is intentional. The export writes a ZIP archive containing audit records, signatures, and system manifest data.

```bash
phlo compliance export-evidence --output evidence.zip --created-by <operator-id>
```

The command requires `--output` and `--created-by`. You can also provide `--domain`, `--description`, `--audit-records`, `--signatures`, and `--manifest`.

## 4. Verify the archive

Before verification, confirm that the archive is the evidence pack you intend to submit.

```bash
phlo compliance verify-evidence evidence.zip
```

Verification checks the HMAC-SHA256 signature over canonical `checksums.json` bytes. A pack can contain internally consistent checksums and still fail verification when its external key material is missing or incorrect.

## Verify

Check the command exit status and keep the verified ZIP with the audit and run identifiers used to create it. Use the Dagster UI and Observatory run report when you need to connect the evidence pack to a particular run.

## Related

- [Evidence, audit, and compliance](../concepts/evidence-audit-and-compliance.md)
- [Run in production](run-in-production.md#release-artifact-support-boundary)
- [Auth and access](../reference/auth-and-access.md)
