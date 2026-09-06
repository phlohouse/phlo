# phlo-airbyte

Airbyte control-plane integration plugin for Phlo.

## Description

`phlo-airbyte` treats Airbyte as a **connector execution engine, not a second
orchestrator**. Dagster remains the scheduler: a Phlo asset
(`AirbyteConnectionAsset`) starts one sync on a pre-existing Airbyte
connection, polls its job, fails closed on unknown or ambiguous terminal
states, and only then emits a materialization carrying the job id, connection
id, output tables, and timestamps as lineage evidence.

Source credentials live in Airbyte's secret store; Phlo config never stores
them.

> **Boundary note:** connector execution requires the full self-managed
> Airbyte stack (workers, temporal, webapp), deployed via `abctl` or an
> external installation. The pinned `airbyte/server` service here provides
> the control-plane API Phlo integrates with. The Iceberg destination
> contract is under an active compatibility spike against the pinned
> release; if it cannot land schema-evolving output in the required Iceberg
> layout, the approved fallback keeps Airbyte for extraction and delegates
> the final Iceberg write to Phlo's existing dlt path.

## Installation

```bash
pip install phlo-airbyte
# or
phlo plugin install airbyte
```

## Configuration

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `AIRBYTE_PORT` | `10020` | Airbyte server API host port |
| `AIRBYTE_WORKSPACE_ID` | _(empty)_ | Airbyte workspace id for connection lookups |
| `AIRBYTE_POLL_INTERVAL_SECONDS` | `10` | Seconds between sync job status polls |
| `AIRBYTE_SYNC_TIMEOUT_SECONDS` | `3600` | Max seconds to wait for one sync |

## Usage

```python
from phlo_airbyte.assets import phlo_airbyte_connection

phlo_airbyte_connection(
    connection_id="<airbyte-connection-uuid>",
    tables=["bronze.postgres_users"],
    group="ingestion",
    name="postgres_users",
)
```

```bash
phlo airbyte status
phlo airbyte connections
phlo airbyte sync <connection-id>
```
