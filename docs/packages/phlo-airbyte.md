# phlo-airbyte

Airbyte control-plane integration plugin for Phlo.

## Overview

`phlo-airbyte` treats Airbyte as a connector execution engine, not a second
orchestrator. Dagster owns scheduling; an `AirbyteConnectionAsset` starts one
sync on a pre-existing Airbyte connection, polls its job, fails closed on
unknown or ambiguous terminal states, and emits job id, connection id, output
tables, and timestamps as lineage evidence. Source credentials stay in
Airbyte's secret store.

> **Boundary note:** connector execution requires the full self-managed
> Airbyte stack (deployed via `abctl` or externally); the pinned
> `airbyte/server` service provides the control-plane API. The Iceberg
> destination contract is under an active compatibility spike; the approved
> fallback delegates the final Iceberg write to Phlo's existing dlt path.

### Key features

- Digest-pinned `airbyte/server` control plane with health checks
- `AirbyteClient` with fail-closed job-state classification
- `phlo_airbyte_connection` asset decorator with sync evidence metadata
- CLI: `phlo airbyte status | connections | sync`

## Installation

```bash
pip install phlo-airbyte
```

## Configuration

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `AIRBYTE_PORT` | `10020` | Airbyte server API host port |
| `AIRBYTE_WORKSPACE_ID` | _(empty)_ | Workspace id for connection lookups |
| `AIRBYTE_POLL_INTERVAL_SECONDS` | `10` | Sync status poll interval |
| `AIRBYTE_SYNC_TIMEOUT_SECONDS` | `3600` | Max wait for one sync |

## Usage

```python
from phlo_airbyte.assets import phlo_airbyte_connection

phlo_airbyte_connection(
    connection_id="<uuid>",
    tables=["bronze.postgres_users"],
    group="ingestion",
)
```
