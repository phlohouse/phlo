# CLI reference

Phlo exposes the `phlo` command and provider commands through Click entry points. The root command reports the installed version with `--version`.

Run assets through Dagster with `phlo materialize` and `phlo backfill`. Provider command groups such as `phlo dbt`, `phlo sling`, and `phlo trino` call the provider directly for inspection and debugging. Runs made through them are not recorded by Dagster.

Regenerate the help dump with:

```bash
for c in $(phlo --help | sed -n '/^Commands:/,/^$/s/^  \([a-z-]*\).*/\1/p'); do printf '\n===== phlo %s --help =====\n' "$c"; phlo "$c" --help; done
```

## Global options

| Option | Meaning |
| --- | --- |
| `--version` | Print the installed Phlo version. |
| `--help` | Show root or command help. |

## phlo airbyte

Interact with the Airbyte control plane (status, connections, sync).

This command accepts no subcommands or options beyond `--help`.

These commands bypass Dagster. Use `phlo materialize` for normal runs.

## phlo alerts

Alert management and configuration.

| Subcommand | What it does |
| --- | --- |
| `list` | List configured alert destinations. |
| `status` | Check alert system status. |
| `test` | Send a test alert to configured destinations. |

## phlo audit

Inspect local Phlo audit records.

| Subcommand | What it does |
| --- | --- |
| `query` | Query audit records. |
| `tail` | Tail recent audit records. |

## phlo authz

Manage RBAC authorisation policies and backend synchronisation.

| Subcommand | What it does |
| --- | --- |
| `plan` | Create a sync plan without applying changes. |
| `revert` | Revert previously applied policy changes. |
| `sync` | Synchronise RBAC policies to backend-native enforcement. |
| `validate` | Validate RBAC configuration files. |
| `verify` | Verify backend state matches desired RBAC state. |

## phlo backfill

Run asset materialisation across a date range with parallel execution.

Usage: `phlo backfill [OPTIONS] [ASSET_NAME]`

| Option | Meaning |
| --- | --- |
| `--start-date TEXT` | Start date (YYYY-MM-DD) |
| `--end-date TEXT` | End date (YYYY-MM-DD) |
| `--partitions TEXT` | Comma-separated partition dates (YYYY-MM-DD,YYYY-MM-DD,...) |
| `--parallel INTEGER` | Number of concurrent partitions to process (default: 1. WAP runs serialise through promotion) |
| `--resume` | Resume last backfill, skipping completed partitions |
| `--dry-run` | Show what would be executed without running |
| `--delay FLOAT` | Delay between parallel executions in seconds (rate limiting) |
| `--json` | Emit a structured result. |

## phlo branch

Manage Nessie branches for data versioning.

| Subcommand | What it does |
| --- | --- |
| `create` | Create a new branch. |
| `delete` | Delete a branch. |
| `diff` | Show differences between branches. |
| `list` | List all branches. |
| `merge` | Merge source branch into target branch. |

## phlo catalog

Manage the lakehouse catalog (Nessie-backed).

| Subcommand | What it does |
| --- | --- |
| `describe` | Show detailed table metadata. |
| `history` | Show table snapshot history. |
| `tables` | List all Iceberg tables in the catalog. |

## phlo clickhouse

Query and inspect the ClickHouse data plane service.

| Subcommand | What it does |
| --- | --- |
| `query` | Execute a SQL query against the running ClickHouse service via... |
| `status` | Show ClickHouse service status: version, uptime, and current... |

## phlo clickstack

Query and inspect the ClickStack service.

| Subcommand | What it does |
| --- | --- |
| `query` | Execute a ClickHouse query against the running ClickStack service. |

## phlo commands

List installed commands and their output, preview, and confirmation options.

| Option | Meaning |
| --- | --- |
| `--json` | Emit installed command metadata as JSON. |

## phlo compliance

Manage compliance features and evidence.

| Subcommand | What it does |
| --- | --- |
| `export-evidence` | Export a compliance evidence pack. |
| `verify-evidence` | Verify the integrity of an evidence pack. |

## phlo config

Manage infrastructure configuration.

| Subcommand | What it does |
| --- | --- |
| `show` | Show the effective infrastructure configuration. |
| `upgrade` | Upgrade phlo.yaml through ordered detect -> plan -> apply ->... |
| `validate` | Validate infrastructure configuration in phlo.yaml. |

## phlo contracts

Schema registry and data contract management.

| Subcommand | What it does |
| --- | --- |
| `check` | Check schema compatibility for a table against its previous... |
| `snapshot` | Snapshot a schema from a JSON file into the registry. |

## phlo dataset

Dataset workflow commands.

| Subcommand | What it does |
| --- | --- |
| `list` | List canonical projections for every governed table. |
| `migrate-overlay` | Plan, apply, or discard the legacy Observatory dataset... |
| `show` | Show the canonical Dataset projection for one Dataset ID. |
| `transition` | Apply one authorized Dataset transition through the... |

## phlo dbt

Dbt commands (compile, run, test, publishing).

| Subcommand | What it does |
| --- | --- |
| `compile` | Compile dbt models in the local project. |
| `publishing` | Manage publishing configuration. |
| `run` | Run dbt models in the local project. |
| `test` | Run dbt tests in the local project. |

These commands bypass Dagster. Use `phlo materialize` for normal runs.

## phlo dev

Start the Dagster development server for your workflows.

| Option | Meaning |
| --- | --- |
| `--host TEXT` | Host to bind to |
| `--port INTEGER` | Port to bind to |
| `--workflows-path TEXT` | Path to workflows directory |

## phlo doctor

Diagnose local Phlo setup and service health.

| Option | Meaning |
| --- | --- |
| `--json` | Output diagnostics as JSON. |
| `--verbose` | Include exception details where available. |

## phlo env

Manage environment configuration.

| Subcommand | What it does |
| --- | --- |
| `export` | Export the generated environment configuration. |

## phlo governance

Check and export governance readiness from Phlo declarations.

| Subcommand | What it does |
| --- | --- |
| `check` | Validate governed tables for publish and production readiness. |
| `export` | Export the browser-safe governance read model. |

## phlo hasura

Hasura GraphQL metadata management CLI.

| Subcommand | What it does |
| --- | --- |
| `apply` | Apply Hasura metadata from a previously exported JSON... |
| `auto-setup` | Run track, relationships, and permissions in sequence... |
| `export` | Export the complete Hasura metadata (tracked tables,... |
| `permissions` | Create default SELECT permissions for standard roles... |
| `relationships` | Analyse foreign key constraints in the schema and... |
| `status` | Show a summary of all tracked tables organised by... |
| `sync-permissions` | Apply permission configurations from a YAML or JSON... |
| `track` | Auto-discover and track tables in Hasura, optionally... |

## phlo init

Initialise a new Phlo project.

| Option | Meaning |
| --- | --- |
| `--template TEXT` | Project template to use  [default: minimal] |
| `--force` | Initialise in non-empty directory |
| `--list-templates` | List available project templates and exit. |
| `--json` | Emit machine-readable JSON. |

## phlo kafka

Interact with the Kafka broker (status, topics).

This command accepts no subcommands or options beyond `--help`.

Forwards arguments to the Kafka provider.

These commands bypass Dagster. Use `phlo materialize` for normal runs.

## phlo lineage

Asset dependency and lineage visualisation commands.

| Subcommand | What it does |
| --- | --- |
| `column` | Column-level lineage commands. |
| `export` | Export lineage to external visualisation formats. |
| `impact` | Analyse downstream impact of an asset. |
| `show` | Display asset dependencies in ASCII tree format. |
| `status` | Show lineage graph status and statistics. |

## phlo logs

View logs from Phlo infrastructure services.

| Option | Meaning |
| --- | --- |
| `-s, --service, --package TEXT` | Service/package to include. Repeat or use commas to select several. |
| `-f, --follow` | Follow log output |
| `-n, --tail, --lines INTEGER RANGE` | Number of recent lines to show before streaming.  [default: 100. x>=0] |
| `--since TEXT` | Show logs since a timestamp or duration supported by Compose. |
| `--until TEXT` | Show logs before a timestamp or duration supported by Compose. |
| `--timestamps` | Show log timestamps. |
| `--no-color` | Disable coloured log output where supported. |
| `--backend [docker|podman|auto]` | Container backend for this command. |

## phlo materialize

Materialise Dagster assets via the configured container backend.

| Option | Meaning |
| --- | --- |
| `-p, --partition TEXT` | Partition date (YYYY-MM-DD) |
| `--no-default-partition` | Do not default the partition to today when --partition is omitted |
| `--select TEXT` | Asset selector expression |
| `--no-contract-refresh` | Skip automatic schema contract refresh before materialisation |
| `--dry-run` | Show command without executing |
| `--json` | Emit a structured result. |

## phlo mcp

Run and inspect the Phlo MCP server.

| Subcommand | What it does |
| --- | --- |
| `config` | Print resolved MCP configuration with secrets redacted. |
| `install` | Print or write an MCP client configuration snippet. |
| `prompts` | List prompts registered by the local MCP server. |
| `serve` | Serve the Phlo MCP server. |
| `tools` | List tools registered by the local MCP server. |

## phlo metrics

Pipeline and data metrics exposure.

| Subcommand | What it does |
| --- | --- |
| `asset` | Show per-asset metrics. |
| `export` | Export metrics to JSON, CSV, or Prometheus text. |
| `summary` | Show key metrics overview. |

## phlo migrate

Data migration commands.

| Subcommand | What it does |
| --- | --- |
| `decorators-2026-05` | Migrate May 2026 decorator APIs. |
| `list` | List available migration spec files. |
| `run` | Execute a migration spec. |
| `status` | Show recent migration history. |
| `validate` | Validate a migration spec without executing. |

## phlo minio

Run MinIO client (mc) commands inside the project service container. This is the main entry point for MinIO CLI operations. It handles common subcommands like 'ls' and 'admin info' with dedicated handlers, while passing other commands directly to the mc binary.

This command accepts no subcommands or options beyond `--help`.

## phlo openmetadata

Manage OpenMetadata integration (optional): check health and sync catalog tables and dbt documentation.

| Subcommand | What it does |
| --- | --- |
| `health` | Check OpenMetadata connectivity using configured credentials.... |
| `sync` | Sync Nessie catalog tables (and optionally dbt docs) into... |

## phlo operations

Guarded plan-first operations (maintenance, backup, restore, upgrade).

| Subcommand | What it does |
| --- | --- |
| `backup` | Create and verify immutable v1 backup sets (ADR 0049 §3). |
| `maintenance` | Plan and apply v1 table maintenance (compaction, snapshot... |
| `restore` | Plan and apply an explicit-target restore (ADR 0049 §4). |
| `upgrade` | Prove the supported deployment upgrade pair (ADR 0049 §5). |

## phlo plugin

Manage Phlo plugins.

| Subcommand | What it does |
| --- | --- |
| `check` | Validate installed plugins. |
| `create` | Create scaffolding for a new plugin. |
| `info` | Show detailed plugin information. |
| `install` | Install a plugin from the registry (wraps pip). |
| `list` | List all discovered plugins. |
| `search` | Search plugin registry. |
| `update` | Update installed plugins based on registry versions. |

## phlo polaris

Manage the Polaris catalog service (status, bootstrap, migration).

This command accepts no subcommands or options beyond `--help`.

## phlo postgres

Run psql or PostgreSQL helper commands against the project database.

This command accepts no subcommands or options beyond `--help`.

## phlo postgrest

PostgREST API management commands.

| Subcommand | What it does |
| --- | --- |
| `generate-views` | Generate PostgREST API views from dbt models. |
| `reload-schema` | Reload PostgREST's schema cache after migrations or... |
| `setup-auth` | Set up PostgREST authentication infrastructure. |

## phlo schema

Manage Pandera schemas and schema validation.

| Subcommand | What it does |
| --- | --- |
| `diff` | Compare a schema version against an older one. |
| `generate` | Generate Pandera schemas from a bounded DLT inference sample. |
| `list` | List all available Pandera schemas with name, field count,... |
| `show` | Show a schema's fields, types, constraints, and descriptions. |
| `validate` | Validate a schema file's syntax and common integration issues. |

## phlo schema-migrate

Schema migration between quality schemas and storage tables.

| Subcommand | What it does |
| --- | --- |
| `apply` | Apply schema migration to a storage table. |
| `diff` | Show pending schema changes between quality... |
| `export-contract` | Export a Phlo contract snapshot for a table. |
| `history` | Show schema version history for a table. |
| `plan` | Generate a migration plan for a table. |
| `scaffold-yaml` | Generate migration scaffold YAML from a Phlo... |
| `scaffold-yaml-recent` | Generate migration scaffold YAML files for recent... |

## phlo services

Manage Phlo infrastructure services (Docker).

| Subcommand | What it does |
| --- | --- |
| `add` | Add optional services or profiles to the rendered project... |
| `exec` | Run a command inside a running Phlo service container. |
| `init` | Initialise Phlo infrastructure in .phlo/ directory. |
| `list` | List available services with status and configuration. |
| `logs` | View logs from Phlo infrastructure services. |
| `migrate` | Move personal files aside and make existing .phlo... |
| `ports` | Show port mappings for all services. |
| `preflight` | Evaluate production readiness for the generated stack. |
| `remove` | Remove a service from the project. |
| `reset` | Reset Phlo infrastructure by stopping services and deleting... |
| `restart` | Restart Phlo infrastructure services. |
| `start` | Start Phlo infrastructure services. |
| `status` | Show status of Phlo infrastructure services. |
| `stop` | Stop Phlo infrastructure services. |

## phlo sling

Sling replication commands.

| Subcommand | What it does |
| --- | --- |
| `conns` | List available Sling connections. |
| `discover` | Discover available streams from a Sling connection. |
| `run` | Run a Sling replication. |

These commands bypass Dagster. Use `phlo materialize` for normal runs.

## phlo status

Show current state of assets, jobs, and services: asset materialisation status and freshness, service health (Dagster, Trino, MinIO, Nessie), with colour-coded indicators. Options narrow the view to assets or services, filter by group or staleness, and emit JSON for scripting. Query failures are logged as warnings.

| Option | Meaning |
| --- | --- |
| `--assets` | Show assets only |
| `--services` | Show services only |
| `--group TEXT` | Filter by asset group |
| `--stale` | Show only stale assets |
| `--json` | JSON output for scripting |

## phlo support

Inspect the bundled, offline support contract.

| Subcommand | What it does |
| --- | --- |
| `status` | Compare installed Phlo artifacts with the bundled release set. |

## phlo test

Run tests for Phlo workflows.

| Option | Meaning |
| --- | --- |
| `--local` | Run tests locally without Docker |
| `--coverage` | Generate coverage report |
| `-v, --verbose` | Verbose output |
| `-m, --marker TEXT` | Run tests with specific pytest marker |

## phlo trino

Run the Trino shell or a Trino-specific helper command.

This command accepts no subcommands or options beyond `--help`.

## phlo validate-schema

Validate a Pandera schema file for valid DataFrameModel syntax, field descriptions, constraints, and type annotations. Exits 0 when valid and 1 when issues are found.

| Option | Meaning |
| --- | --- |
| `--check-constraints` | Check that constraints are defined (default: True) |
| `--check-descriptions` | Check that fields have descriptions (default: True) |

## phlo validate-workflow

Validate a workflow asset file for decorator usage, unique_key presence, cron validity, function signature, and return types before deployment. Exits 0 when valid and 1 when issues are found.

| Option | Meaning |
| --- | --- |
| `--fix` | Auto-fix issues where possible |

## phlo workflow

Manage workflows.

| Subcommand | What it does |
| --- | --- |
| `check` | Validate a workflow and its inferred schema before... |
| `create` | Create a workflow scaffold. |
