# CLI reference

Phlo exposes the `phlo` command and provider commands through Click entry points. The root command reports the installed version with `--version`.

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

Airbyte control-plane commands from `phlo-airbyte`.

| Subcommand | What it does |
| --- | --- |
| `connections` | Inspect Airbyte connections. |
| `sources` | Inspect Airbyte sources. |
| `destinations` | Inspect Airbyte destinations. |
| `sync` | Run an Airbyte synchronization. |

## phlo alerts

Alert destination commands from the alerting package.

| Subcommand | What it does |
| --- | --- |
| `list` | List configured alert destinations. |
| `test` | Send a destination test event. |

## phlo audit

Audit event commands from the core audit surface.

| Subcommand | What it does |
| --- | --- |
| `events` | List or inspect audit events. |
| `verify` | Verify audit records. |

## phlo authz

Authorization inspection commands from `phlo-api`.

| Subcommand | What it does |
| --- | --- |
| `check` | Evaluate access for a principal and route. |
| `explain` | Show the route guard decision inputs. |

## phlo backfill

Launch a backfill for a registered asset.

| Option | Meaning |
| --- | --- |
| `--start` | Inclusive start partition. |
| `--end` | Inclusive end partition. |
| `--partition` | Run one partition. |
| `--select` | Select an asset or selection expression. |
| `--dry-run` | Show the launch without executing it. |
| `--json` | Emit a JSON result envelope. |

## phlo branch

Manage versioned catalog branches.

| Subcommand | What it does |
| --- | --- |
| `create` | Create a catalog branch. |
| `delete` | Delete a catalog branch. |
| `list` | List catalog branches. |
| `merge` | Merge a branch. |

## phlo catalog

Inspect catalog tables through a catalog provider.

| Subcommand | What it does |
| --- | --- |
| `tables` | List tables. |
| `describe` | Show a table schema and properties. |
| `history` | Show table snapshot history. |

`describe` and `history` accept a table name and support a catalog reference option.

## phlo clickhouse

ClickHouse service commands from `phlo-clickhouse`.

| Subcommand | What it does |
| --- | --- |
| `status` | Show ClickHouse service state. |
| `query` | Run a ClickHouse query. |

## phlo clickstack

ClickStack service commands from `phlo-clickstack`.

| Subcommand | What it does |
| --- | --- |
| `status` | Show ClickStack service state. |

## phlo commands

Inspect commands registered by installed plugins.

| Subcommand | What it does |
| --- | --- |
| `list` | List command paths. |
| `describe` | Describe a command path. |

## phlo compliance

Evaluate compliance checks for the current project.

| Subcommand | What it does |
| --- | --- |
| `check` | Run compliance checks. |
| `report` | Render a compliance report. |

## phlo config

Inspect resolved project configuration.

| Subcommand | What it does |
| --- | --- |
| `show` | Show configuration values. |
| `validate` | Validate `phlo.yaml`. |

## phlo contracts

Inspect data contract declarations.

| Subcommand | What it does |
| --- | --- |
| `list` | List contracts. |
| `show` | Show a contract. |

## phlo dataset

Manage dataset publication and migration state.

| Subcommand | What it does |
| --- | --- |
| `list` | List datasets. |
| `publish` | Publish a dataset declaration. |
| `discard` | Discard an overlay. |
| `migrate` | Plan or execute a dataset migration. |

## phlo dbt

Run dbt provider commands from `phlo-dbt`.

| Subcommand | What it does |
| --- | --- |
| `run` | Run dbt models. |
| `test` | Run dbt tests. |
| `build` | Run dbt build. |

## phlo dev

Run local development helpers.

| Subcommand | What it does |
| --- | --- |
| `shell` | Open a project shell with Phlo environment values. |
| `watch` | Watch project files. |

## phlo doctor

Run environment, project, port, and live service diagnostics.

| Option | Meaning |
| --- | --- |
| `--json` | Emit diagnostics as JSON. |
| `--verbose` | Include diagnostic details. |

## phlo env

Inspect resolved environment values.

| Subcommand | What it does |
| --- | --- |
| `show` | Show environment keys and sources. |
| `validate` | Validate environment configuration. |

## phlo governance

Inspect governance metadata.

| Subcommand | What it does |
| --- | --- |
| `list` | List governance declarations. |
| `show` | Show one declaration. |

## phlo hasura

Hasura integration commands from `phlo-hasura`.

| Subcommand | What it does |
| --- | --- |
| `status` | Show Hasura state. |
| `metadata` | Inspect Hasura metadata. |

## phlo init

Initialize a new Phlo project. Creates a minimal project structure for using Phlo as an installable package. Users only need to maintain workflow files, not the entire framework.

| Option | Meaning |
| --- | --- |
| `--template TEXT` | Select a template. |
| `--force` | Allow initialization in an existing destination. |
| `--list-templates` | List discovered templates. |
| `--json` | Emit machine-readable output. |

The default template is `minimal`.

## phlo kafka

Kafka ingestion commands from `phlo-kafka`.

| Subcommand | What it does |
| --- | --- |
| `topics` | Inspect Kafka topics. |
| `consume` | Run a consumer. |

## phlo lineage

Asset dependency and lineage visualization commands. This command group provides tools for exploring data lineage, including ASCII trees, external exports, impact analysis, and column-level lineage.

| Subcommand | What it does |
| --- | --- |
| `graph` | Show a lineage graph. |
| `list` | List lineage edges. |

## phlo logs

View logs from Phlo infrastructure services.

| Option | Meaning |
| --- | --- |
| `--service TEXT` | Filter by service. |
| `--follow` | Follow new log records. |
| `--tail INTEGER`, `--lines INTEGER` | Number of recent lines to show before streaming. Default `100`. |
| `--since TEXT` | Show logs since a timestamp or duration supported by Compose. |
| `--until TEXT` | Show logs before a timestamp or duration supported by Compose. |
| `--timestamps` | Show log timestamps. |
| `--no-color` | Disable colored log output where supported. |
| `--backend [docker\|podman\|auto]` | Container backend for this command. |

## phlo materialize

Materialize Dagster assets via the configured container backend.

| Option | Meaning |
| --- | --- |
| `[ASSET_NAME]` | Asset key to materialize. |
| `--partition TEXT` | Materialize one partition. |
| `--no-default-partition` | Disable the default partition. |
| `--select TEXT` | Select an asset expression. |
| `--no-contract-refresh` | Skip contract refresh. |
| `--dry-run` | Plan without execution. |
| `--json` | Emit a JSON result envelope. |

## phlo mcp

Run and inspect the Phlo MCP server.

| Subcommand | What it does |
| --- | --- |
| `serve` | Run the MCP server. |
| `tools` | List MCP tools. |
| `resources` | List MCP resources. |

## phlo metrics

Display runtime metrics.

| Option | Meaning |
| --- | --- |
| `--json` | Emit metrics as JSON. |
| `--limit INTEGER` | Limit displayed runs. |

## phlo migrate

Manage recorded data migrations.

| Subcommand | What it does |
| --- | --- |
| `plan` | Show pending migrations. |
| `apply` | Apply migrations. |
| `status` | Report migration status. |
| `list` | List migration specifications. |

## phlo minio

MinIO commands from `phlo-minio`.

| Subcommand | What it does |
| --- | --- |
| `status` | Show MinIO state. |
| `buckets` | Inspect buckets. |

## phlo openmetadata

OpenMetadata commands from `phlo-openmetadata`.

| Subcommand | What it does |
| --- | --- |
| `status` | Show OpenMetadata state. |
| `sync` | Synchronize metadata. |

## phlo operations

Plan and execute catalog operations.

| Subcommand | What it does |
| --- | --- |
| `plan` | Plan an operation. |
| `execute` | Execute an approved operation. |
| `status` | Show operation status. |

## phlo plugin

Inspect, scaffold, validate, install, and manage plugins.

| Subcommand | What it does |
| --- | --- |
| `create` | Scaffold a plugin. |
| `check` | Run plugin checks. |
| `install` | Install a plugin. |
| `list` | List installed plugins. |
| `remove` | Remove a plugin. |

## phlo polaris

Polaris catalog commands from `phlo-polaris`.

| Subcommand | What it does |
| --- | --- |
| `status` | Show Polaris state. |
| `branches` | Inspect catalog branches. |

## phlo postgres

Postgres service commands from `phlo-postgres`.

| Subcommand | What it does |
| --- | --- |
| `status` | Show Postgres state. |
| `query` | Run a Postgres query. |

## phlo postgrest

PostgREST commands from `phlo-postgrest`.

| Subcommand | What it does |
| --- | --- |
| `status` | Show PostgREST state. |

## phlo schema

Inspect schema declarations.

| Subcommand | What it does |
| --- | --- |
| `list` | List schemas. |
| `show` | Show a schema. |

## phlo schema-migrate

Plan and execute schema migrations.

| Subcommand | What it does |
| --- | --- |
| `plan` | Classify schema changes. |
| `apply` | Apply a schema migration. |
| `status` | Show migration status. |

## phlo services

Manage generated Docker or Podman services.

| Subcommand | What it does |
| --- | --- |
| `add` | Add a service package. |
| `exec` | Execute a command in a service. |
| `init` | Generate `.phlo/` infrastructure files. |
| `list` | List discovered services. |
| `logs` | Show service logs. |
| `migrate` | Migrate service configuration. |
| `ports` | Show configured ports. |
| `preflight` | Check service prerequisites. |
| `remove` | Remove a service package. |
| `reset` | Reset generated service state. |
| `restart` | Restart services. |
| `start` | Start services. |
| `status` | Show service status and published ports. |
| `stop` | Stop services, with optional volume removal. |

## phlo sling

Sling replication commands from `phlo-sling`.

| Subcommand | What it does |
| --- | --- |
| `replicate` | Run a Sling replication. |
| `list` | List replication assets. |

## phlo status

Show project and runtime status.

| Option | Meaning |
| --- | --- |
| `--json` | Emit status as JSON. |

## phlo support

Show package and service support status from the support manifest.

| Option | Meaning |
| --- | --- |
| `--json` | Emit support status as JSON. |

## phlo test

Run project tests and workflow checks.

| Option | Meaning |
| --- | --- |
| `[ASSET_NAME]` | Limit checks to an asset. |
| `--local` | Use local execution mode. |
| `--coverage` | Collect coverage. |
| `-v`, `--verbose` | Increase test output. |
| `-m`, `--marker TEXT` | Select tests by marker. |

## phlo trino

Run the Trino shell or pass helper arguments to the shell. The command is provided by `phlo-trino`.

| Option | Meaning |
| --- | --- |
| `--catalog TEXT` | Set the query session catalog. |
| `--schema TEXT` | Set the query session schema. |
| `TRINO_ARGS` | Additional Trino shell arguments. |

## phlo validate-schema

Validate schema declarations.

| Option | Meaning |
| --- | --- |
| `--json` | Emit validation results as JSON. |

## phlo validate-workflow

Validate discovered workflow definitions.

| Option | Meaning |
| --- | --- |
| `--json` | Emit validation results as JSON. |

## phlo workflow

Inspect discovered workflows.

| Subcommand | What it does |
| --- | --- |
| `list` | List workflows. |
| `show` | Show a workflow. |
