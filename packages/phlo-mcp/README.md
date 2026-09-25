# phlo-mcp

MCP server for Phlo observability and lakehouse operations.

## Overview

`phlo-mcp` exposes curated read-only MCP tools over Phlo's observability and
operator surfaces. It sits on top of `phlo-api` and gives MCP clients a stable,
agent-friendly surface for lakehouse inspection.

## Install

```bash
uv pip install -e packages/phlo-mcp
```

## Exposed tools

- `get_platform_health`
- `list_plugins`
- `get_service_status`
- `get_recent_alerts`
- `get_dashboard_links`
- `get_operation_context`
- `get_logs_query_link`
- `get_metrics_query_link`
- `get_materialization_history`
- `get_run_logs`
- `get_run_trace_spans`
- `get_trace_spans`
- `render_trace_spans_tree`
- `inspect_materialization`
- `get_asset_materialization_trace`
- `render_materialization_trace_tree`
- `render_run_trace_tree`

Optional guarded operational tools:

- `materialize_asset`
- `retry_failed_run`
- `cancel_run`
- `backfill_asset`
- `list_partitions`
- `create_workflow`
- `validate_workflow`
- `validate_schema`
- `lint_project`
- `install_plugin`
- `get_run_status`

Resources:

- `phlo://runtime/config`
- `phlo://runtime/services`
- `phlo://runtime/services/{service_name}`
- `phlo://runtime/plugins`
- `phlo://runtime/assets`
- `phlo://runtime/assets/{asset_key_path}`
- `phlo://runtime/schemas/{asset_key_path}`
- `phlo://runtime/contracts`
- `phlo://runtime/contracts/{table_name}`
- `phlo://runtime/operations/{operation_id}`
- `phlo://runtime/dashboards`
- `phlo://docs/packages/{package_name}`
- `phlo://docs/cli`
- `phlo://docs/mcp/tools`
- `phlo://docs/mcp/prompts`

Run-level and materialization tools rely on the backing `phlo-api` having access
to Dagster asset history, Loki log queries, and ClickStack OTEL trace storage.
Trace tools can be filtered by run id, asset key, job name, service name, span
name, status code, and start/end time.
Resource URIs are read-only and deterministic so MCP clients can attach Phlo
runtime context without invoking parameterized tools.
`phlo://runtime/operations/{operation_id}` returns the same v1 operation
observability contract exposed by `phlo-api`, including stable operation, trace,
log, metric, and incident identifiers.

When real spans are available, rendered trees include:
- span kind
- status code
- duration
- selected Phlo attributes like stage, asset key, job name, and operation

## Usage

Start a backing API first:

```bash
uv run --package phlo-api uvicorn phlo_api.main:app --host 127.0.0.1 --port 4000
```

Run the MCP server over stdio:

```bash
phlo-mcp --api-base-url http://127.0.0.1:4000
```

For protected `phlo-api` instances, provide a bearer token:

```bash
phlo-mcp \
  --api-base-url http://127.0.0.1:4000 \
  --api-token "$PHLO_API_TOKEN"
```

Guarded operational tools are disabled by default. To expose asset
materialization, retry, cancel, backfill, and authoring tools, set
`PHLO_MCP_ENABLE_WRITE_TOOLS=true` or pass `--enable-write-tools` with an API
token. Write tools return structured `audit_context` metadata and default to
`dry_run=true` where supported. `phlo-api` enforces scopes, idempotency keys,
rate limits, and API-side audit logging before dispatching live Dagster
operations through the `phlo-dagster` capability adapter.

HTTP and SSE transports accept loopback binds only (`127.0.0.1` or `::1`).
`PHLO_MCP_API_TOKEN` authenticates outbound requests to phlo-api, not inbound
MCP clients. For remote access, keep MCP bound to loopback and put an
authenticated reverse proxy in front of it.

Required scopes:

| Tool | Scope |
|---|---|
| read/inspect/search/log/trace tools | `lakehouse:read` |
| `materialize_asset`, `retry_failed_run`, `cancel_run`, `backfill_asset` | `lakehouse:operate` |
| `create_workflow`, `validate_workflow`, `validate_schema`, `lint_project` | `project:write` |
| `install_plugin` | `admin` |
| `list_partitions`, `get_run_status` | `lakehouse:read` |

Claude Code example:

```json
{
  "mcpServers": {
    "phlo": {
      "command": "uv",
      "args": ["run", "--package", "phlo-mcp", "phlo-mcp", "--api-base-url", "http://127.0.0.1:4000"]
    }
  }
}
```

Example prompts once connected:

- "Get the latest materializations for `silver/orders`."
- "Fetch logs for run `abc-123`."
- "Inspect the latest materialization for `silver/orders`."
- "Get the latest materialization trace for `silver/orders`."
- "Render the materialization trace tree for `silver/orders`."
- "Get OTEL spans for run `abc-123`."
- "Get failed Dagster spans for asset `silver/orders` in the last hour."
- "Render the run trace tree for `abc-123`."
- "Render a trace tree for job `daily_orders`."

Run it over streamable HTTP:

```bash
phlo-mcp \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000 \
  --path /mcp \
  --api-base-url http://127.0.0.1:4000
```

Optional local span capture:

```bash
phlo-mcp --trace-file .phlo/phlo-mcp-trace.jsonl
```

## Live stack smoke

Run the MCP smoke against a live `phlo-api` service and its configured capability backends:

```bash
uv run python packages/phlo-mcp/tests/smoke_stack.py --start-stack
```

The smoke checks live `phlo-api`, orchestration connectivity, trace filtering
through the observability capability, MCP tool registration, MCP resource
registration, representative MCP resource reads, and a generated
`mcp_smoke_asset` capability fixture.
With `--start-stack`, the fixture is written to `.phlo/mcp-smoke-project`,
which is ignored by git.

When the configured observability backend requires credentials, pass them to
the backing `phlo-api` process with that backend's environment variables.

To verify guarded write-tool registration without calling mutation endpoints:

```bash
uv run python packages/phlo-mcp/tests/smoke_stack.py \
  --enable-write-tools \
  --api-token "$PHLO_MCP_SMOKE_API_TOKEN"
```

To call guarded write tools in dry-run mode, add `--exercise-write-tools` and
`--start-stack`, or provide `--asset-key` when testing an existing project.
This requires the backing `phlo-api` write endpoints to be available.

```bash
uv run python packages/phlo-mcp/tests/smoke_stack.py \
  --start-stack \
  --enable-write-tools \
  --api-token "$PHLO_MCP_SMOKE_API_TOKEN" \
  --exercise-write-tools
```

Use real data assertions when you have a known run or asset:

```bash
uv run python packages/phlo-mcp/tests/smoke_stack.py \
  --run-id abc-123 \
  --require-run-spans \
  --asset-key silver/orders \
  --require-materialization
```
