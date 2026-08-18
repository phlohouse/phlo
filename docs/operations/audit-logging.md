# Audit Logging

Use this page to define Phlo's audit-log posture for production deployments.

## What Counts As Audit Logging

Phlo distinguishes three signal types:

- **Operational logs**: service health, startup, retries, throughput, and debugging detail.
- **Security and audit logs**: authentication events, authorization denials, administrative actions, and storage access events.
- **Query and access trails**: evidence of who queried data, from where, and through which surface.

You need all three in production, but they have different owners and retention expectations.

## Supported Posture

| Surface | Signal Type | Phlo-Owned Path | Production Stance |
|---------|-------------|-----------------|-------------------|
| `phlo-api` / Observatory | Security and audit logs | Structured app logs, optionally exported through `phlo-otel` OTLP logs | Recommended |
| MinIO | Storage audit trail | `MINIO_AUDIT_ENABLED` + `MINIO_AUDIT_ENDPOINT` in the bundled service package | Recommended |
| PostgreSQL | Database audit trail | No bundled `pgaudit` automation today | Operator-managed |
| Trino | Query and access trail | Coordinator logs and UI history are present; centralized retention/export is operator-managed | Recommended with external retention |
| Nessie | Catalog API access trail | Nessie server logs and metrics; centralized retention/export is operator-managed | Optional, based on catalog exposure |

## Default, Recommended, And External

### Enabled by default

- Phlo application logging remains on by default.
- Trino coordinator query history remains available in the Trino UI.

These defaults help with local debugging, but they are not sufficient by themselves to claim a durable audit trail.

### Recommended and Phlo-supported

- Route `phlo-api` and Observatory logs to a centralized log backend through `phlo-otel` with `OTEL_LOGS_EXPORTER=otlp`.
- Enable MinIO audit webhooks with:

```bash
MINIO_AUDIT_ENABLED=on
MINIO_AUDIT_ENDPOINT=http://loki:3100/loki/api/v1/push
```

- Keep application logs and storage audit logs in the same retention domain so an operator can correlate who accessed data with which app action triggered it.

### External or operator-managed

- PostgreSQL `pgaudit` remains external today because the bundled PostgreSQL service does not ship a `pgaudit`-enabled image or bootstrap automation.
- Trino long-term query retention and export remain external. Phlo exposes the running coordinator, but retention policy and downstream archival are still an operator responsibility.
- Nessie audit requirements depend on whether the catalog is exposed beyond internal service-to-service traffic.

## Minimum Production Claim

Treat a Phlo deployment as auditable only when all of the following are true:

- authentication events and authorization denials from `phlo-api` are retained outside the container lifecycle
- MinIO audit logging is enabled and routed to a durable backend
- Trino query history is retained long enough for incident investigation, either in Trino itself or in an external sink
- retention, access control, and backup expectations for the audit backend are documented
- the operator has decided explicitly whether PostgreSQL and Nessie need separate audit controls in the target environment

## Routing Pattern

Phlo's recommended routing pattern is:

```text
application logs -> phlo-otel -> OTLP collector or Alloy -> durable log backend
MinIO audit webhook -------------------------------> durable log backend
Trino / Nessie / PostgreSQL audit sources --------> operator-managed sink
```

Phlo owns the application log envelope and the MinIO webhook configuration surface. Database-grade audit pipelines stay under operator control unless Phlo ships a dedicated integration for them.

## Retention Expectations

Phlo does not impose one global retention period, but the production baseline is:

- keep application and storage audit evidence for the same incident review window
- do not rely on container-local logs as the only audit record
- define who can read, delete, and export audit records
- verify that backup and restore procedures cover the audit backend itself

## Checklist

Before sign-off, verify:

- `OTEL_LOGS_EXPORTER=otlp` is enabled for Phlo application logs, or an equivalent centralized log path exists
- `MINIO_AUDIT_ENABLED=on` and `MINIO_AUDIT_ENDPOINT` points at a durable sink
- the Trino query-history path is known to operators
- retention and access policy for audit records are written down
- any required PostgreSQL or Nessie audit controls are called out explicitly as external dependencies

## Evidence Pack Integrity Key

Evidence packs exported with `phlo compliance export-evidence` are
authenticated with HMAC-SHA256 over the canonical `checksums.json` bytes
using key material held **outside** the archive.  This means an attacker
who can modify the archive and recompute every internal checksum still
cannot produce a pack that verifies as valid without the external key.

### Key configuration

| Variable | Purpose |
|----------|---------|
| `PHLO_EVIDENCE_HMAC_KEY` | Preferred key for evidence-pack authentication. |
| `PHLO_AUDIT_HMAC_KEY` | Fallback, reusing the regulated audit-key contract. |

The key may also be passed as an explicit library argument
(`hmac_key=`) to `create_evidence_pack` and `verify_evidence_pack`.

No development default is generated.  **Export and verification fail
closed when no key material is available.**

### Operator key contract

- Generate a high-entropy key (≥ 32 bytes) and distribute it only to
  systems that export or verify evidence packs.
- The same key must be available at verification time.  A different key
  produces a `Key identifier mismatch` error — the pack is not valid.
- `signature.json` inside the archive stores a non-reversible key
  identifier (first 16 hex chars of `SHA-256(key)`), the pack format
  version, algorithm, checksum-envelope digest, and authentication
  value.  **The key itself is never stored in the pack.**
- Verification results and CLI output expose neither the key material
  nor the expected authentication value.
- Unsigned version-1 packs (no `signature.json`) are reported as
  `unsigned`, never `valid`.  Historical packs exported before format
  v2 will not pass verification and must be re-exported with a key.

### What `valid` means

`phlo compliance verify-evidence` reports `valid` only when the pack's
canonical `checksums.json` bytes are authenticated by a valid v2
HMAC-SHA256 signature using the configured key.  Internal checksum
consistency alone is not sufficient.

## Related Pages

- [Security](../setup/security.md)
- [Production Readiness](production-readiness.md)
- [Configuration Reference](../reference/configuration-reference.md)
- [phlo-minio](../packages/phlo-minio.md)
