# polaris-streaming

Streaming lakehouse showcasing Phlo's snapshot-WAP stack end to end:
Apache Polaris as the Iceberg REST catalog, Kafka events landing through
durable checkpoints into candidate branches, audited releases exposed via a
compare-and-swap release pointer, and the Airbyte control plane.

## What it proves (scripts/e2e.py)

| Feature | Package | Proof |
| ------- | ------- | ----- |
| Polaris service, principals, grants | phlo-polaris | live health + bootstrap |
| Iceberg REST catalog writes on MinIO | phlo-polaris | PyIceberg via REST |
| Snapshot WAP: readers see data only at promotion | phlo-polaris | row counts before/after |
| Failed audit: candidates discoverable, release unchanged | phlo-polaris | ledger + pointer |
| CAS refusal on stale revision | phlo-polaris | ReleaseConflictError |
| Checkpoint claim -> stage -> promote -> commit | phlo-kafka | real Kafka + Postgres |
| Replay produces no duplicate logical rows | phlo-kafka | row counts |
| Schema violation -> DLQ + retained offsets | phlo-kafka | events.dlq |
| Airbyte control plane (health, connections) | phlo-airbyte | live API |

## Troubleshooting (compatibility pass notes)

Captured while proving this stack on arm64 Docker Desktop 29.2.1:

- **Polaris 1.7.0 relational persistence**: the `apache/polaris:1.7.0` image does not
  bundle the relational (Postgres) `MetaStoreManagerFactory` — startup fails with
  `No bean found ... identifier "relational"`. The service therefore pins
  `polaris.persistence.type: in-memory`; production deployments should use the
  Helm chart with the relational persistence build (metadata resets on container
  recreate; `phlo polaris bootstrap` re-provisions idempotently).
- **Polaris boot hang**: in this environment the container can stall before the
  Quarkus banner (2 log lines, ~9% CPU, native arm64, not entropy). If
  `/q/health` never responds, recreate the container; `scripts/e2e.py` waits up
  to 15 minutes and fails loudly with `FAIL polaris.health`.
- **Kafka KRaft**: the controller quorum voter must be `1@localhost:9093` and
  `CLUSTER_ID` must be a valid 22-char base64 UUID (both fixed in the package
  service.yaml); otherwise the broker shuts down with "unable to register with
  the controller quorum".
- **Port isolation**: `.phlo/.env.local` remaps host ports to 13xxx so the
  example never collides with a developer's own running stack.

## Run it

```bash
# from this directory (packages installed in the workspace venv)
uv run phlo services start postgres minio polaris kafka airbyte-temporal airbyte-manifest airbyte
uv run --locked --with confluent-kafka python scripts/e2e.py
```

The Airbyte control plane proves health/connection APIs; connector syncs
require the full self-managed Airbyte stack (abctl) per the compatibility
spike in the phlo-airbyte README. Dagster-sensor automation of the same WAP
lifecycle is the production path; this example drives the identical catalog
contract directly for a compact, deterministic demo.
