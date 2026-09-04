# CLI Reference

Complete reference for the Phlo command-line interface.

## Installation

```bash
# Using pip
pip install -e .

# Using uv (recommended)
uv pip install -e .
```

Verify:

```bash
phlo --version
```

## Global Options

```bash
phlo --help              # Show help
phlo --version           # Show version
phlo --quiet             # Reduce non-essential output
phlo --no-color          # Disable colorized terminal output
```

Agent-facing commands prefer `--json` where available and emit the shared
`{"data": ..., "warnings": [...], "errors": [...]}` envelope.

## Command Overview

**Core Commands:**

```bash
phlo init                # Initialize new project
phlo services            # Manage infrastructure services
phlo plugin              # Manage plugins
phlo dev                 # Start development server
phlo test                # Run tests
phlo config              # Configuration management
phlo env                 # Environment exports
phlo doctor              # Diagnose local setup and service health
phlo mcp                 # Serve, install, and inspect the MCP server
phlo audit               # Inspect local MCP/API mutation audit logs
```

**Workflow Commands:**

```bash
phlo workflow create     # Create new workflow
phlo validate-workflow   # Validate workflow configuration
```

**Asset Commands:**

```bash
phlo materialize         # Materialize assets
phlo backfill            # Backfill partitioned assets
phlo status              # Show asset status
phlo logs                # Query structured logs
```

**Data Catalog Commands:**

```bash
phlo branch              # Manage Nessie branches
phlo catalog             # Catalog operations (tables, describe, history)
phlo lineage             # Asset lineage (show, export)
```

**Authorization Commands:**

```bash
phlo authz validate      # Validate canonical RBAC config
phlo authz plan          # Show backend policy changes without applying
phlo authz sync          # Apply canonical RBAC to configured backends
phlo authz verify        # Check backend state against desired RBAC state
```

**Quality Commands:**

```bash
phlo schema              # Manage Pandera schemas
phlo validate-schema     # Validate Pandera schemas
phlo schema-migrate      # Diff, plan, apply, and scaffold table schema migrations
phlo migrate             # Run declarative data migration specs
```

### phlo doctor

Diagnose local Phlo setup, project configuration, service discovery, port mappings, and
running service health.

```bash
phlo doctor
phlo doctor --json
phlo doctor --verbose
```

Use `--json` when attaching diagnostics to bug reports or CI logs. The command is
read-only and does not start, stop, or modify services.

**Optional Plugin Commands** (requires installation):

```bash
phlo postgrest           # PostgREST management
phlo hasura              # Hasura GraphQL management
phlo minio               # MinIO client and admin helpers
phlo postgres            # PostgreSQL shell and maintenance
phlo trino               # Trino shell and queries
phlo dbt                 # dbt compile/run/test helpers
phlo dbt publishing      # dbt publishing layer
phlo metrics             # Metrics summary (built-in)
phlo alerts              # Alerting rules
phlo openmetadata        # OpenMetadata catalog
```

### phlo minio

Run the MinIO client inside the project MinIO service.

```bash
phlo minio [MC_ARGS...]
phlo minio ls [OPTIONS] [TARGET]
phlo minio admin info [OPTIONS] [TARGET]
```

**Examples**:

```bash
# List buckets
phlo minio ls local/

# Inspect warehouse contents
phlo minio ls --recursive local/warehouse/

# Show MinIO admin info
phlo minio admin info --json local/

# Pass through to raw mc commands
phlo minio cp local/file.txt local/warehouse/file.txt
```

### phlo postgres

Run `psql` and common PostgreSQL maintenance tasks inside the project database service.

```bash
phlo postgres [PSQL_ARGS...]
phlo postgres query [OPTIONS] [QUERY]
phlo postgres dump --file PATH
phlo postgres restore --file PATH
phlo postgres vacuum [OPTIONS]
```

**Examples**:

```bash
# Interactive psql shell
phlo postgres

# Connect to a specific database
phlo postgres --dbname analytics

# Run a query
phlo postgres query "SELECT now()"

# Backup and restore
phlo postgres dump --file backups/phlo.sql.gz
phlo postgres restore --file backups/phlo.sql.gz
```

### phlo trino

Run the Trino CLI inside the project Trino service without reaching for `docker exec`.

```bash
phlo trino [TRINO_CLI_ARGS...]
phlo trino query [OPTIONS] [QUERY]
```

**Examples**:

```bash
# Interactive Trino shell
phlo trino

# Interactive shell with catalog/schema preselected
phlo trino --catalog iceberg --schema raw

# Non-interactive query
phlo trino query "SELECT * FROM iceberg.bronze.users LIMIT 10"

# Query from a file
phlo trino query --file query.sql --catalog iceberg --schema bronze
```

### phlo dbt

Run dbt against the project dbt workspace. When `.phlo/` exists, Phlo runs dbt inside the
Dagster service container by default so the command matches the running stack. Use `--local`
to run host dbt directly. Successful `phlo dbt run` commands also import manifest asset
lineage into the configured lineage sink when one is installed.

```bash
phlo dbt compile [OPTIONS]
phlo dbt run [OPTIONS]
phlo dbt test [OPTIONS]
```

**Examples**:

```bash
# Run dbt inside the dagster service
phlo dbt run --select silver.*

# Compile with an explicit target
phlo dbt compile --target prod

# Run host dbt instead of the container
phlo dbt test --local --select gold.*
```

### phlo authz

Manage canonical RBAC configuration and backend synchronization.

```bash
phlo authz validate [OPTIONS]
phlo authz plan [OPTIONS]
phlo authz sync [OPTIONS]
phlo authz verify [OPTIONS]
```

Notes:

- canonical RBAC accepts `allow` policies only
- `deny` policies are rejected during validation and planning
- config is loaded from `.phlo/authorization/roles.yaml` and `.phlo/authorization/policies.yaml`

Common workflow:

```bash
phlo authz validate
phlo authz plan --backend trino
phlo authz sync --backend trino
phlo authz verify --backend trino
```

Command behavior:

- `validate`: parse and validate canonical RBAC config, including role references
  and hierarchy cycles
- `plan`: compile desired backend artifacts and show creates or deletes without
  applying them
- `sync`: apply compiled backend artifacts; use `--dry-run` to skip mutation
- `verify`: compare desired compiled artifacts against current backend state and
  report drift

Options shared across these commands:

- `--path PATH`: path to the `.phlo` directory root
- `--backend TEXT`: restrict work to one or more backends where supported
- `--environment TEXT`: compiler context label, default `development`

Additional sync option:

- `--dry-run`: show sync results without applying changes

Use [Canonical RBAC](canonical-rbac.md) for the full file format and backend
support details.

## Services Commands

Manage local container infrastructure services.

Services are provided by installed service packages (e.g., `phlo-dagster`, `phlo-trino`). Install
core services with:

```bash
uv pip install -e ".[core-services]"
```

Optional services can be added later:

```bash
phlo plugin install phlo-observatory
```

### phlo services init

Initialize infrastructure directory and configuration.

```bash
phlo services init [OPTIONS]
```

**What it does**:

- Creates `.phlo/` directory
- Generates Compose configurations
- Sets up network and volume definitions
- Creates `phlo.yaml` config file

**Options**:

```bash
--force              # Overwrite existing configuration
--name NAME          # Project name (default: directory name)
--dev                # Development mode: mount local phlo source
--service-dev        # Also apply service-specific dev runtimes
--no-dev             # Explicitly disable dev mode
--phlo-source PATH   # Path to phlo repo or src/phlo for dev mode
```

**Examples**:

```bash
# Basic initialization
phlo services init

# Force overwrite existing
phlo services init --force

# With custom project name
phlo services init --name my-lakehouse

# Development mode with local source
phlo services init --dev --phlo-source /path/to/phlo

# Development mode plus service-level dev runtimes
phlo services init --dev --service-dev
```

### phlo services start

Start all infrastructure services.

```bash
phlo services start [OPTIONS]
```

**Options**:

```bash
--native                 # Run native dev services (phlo-api, Observatory) as subprocesses
--profile PROFILE        # Additional service profiles
--service SERVICE        # Start only specific service(s)
--detach, -d             # Run in background
--build                  # Rebuild containers before starting
--backend docker|podman|auto # Container backend for this command
```

Docker Compose v2 is the currently supported backend. Podman is documented as an
experimental, non-CI-supported alternative.

**Profiles**:

- `observability`: Prometheus, Grafana, Loki
- `api`: PostgREST, Hasura
- `catalog`: OpenMetadata

**Examples**:

```bash
# Start core services
phlo services start

# Start with observability
phlo services start --profile observability

# Run Observatory/phlo-api without Docker (useful on ARM Macs)
phlo services start --native

# Develop the phlo framework from a local monorepo (Dagster container installs editable from source)
phlo services init --force --dev --phlo-source /path/to/phlo
phlo services start

# Multiple profiles
phlo services start --profile observability --profile api

# Rebuild and start
phlo services start --build

# Use Podman for this invocation
phlo services start --backend podman
```

**Validation behavior**:

- `--profile` values are validated before container backend commands are executed.
- Unknown profiles fail fast with a `ClickException` that includes valid options.
- `--service` targets include required dependencies and matching `*-setup` bootstrap services.

```bash
phlo services start --profile not-a-profile
# Error: Invalid profile: not-a-profile. Valid profile options: api, observability
```

**Services started**:

- PostgreSQL (port 10000)
- MinIO (ports 10001-10002)
- Nessie (port 10003)
- Trino (port 10005)
- Dagster webserver (port 10006)
- Dagster daemon

### phlo services exec

Run a command inside a running Phlo service container without hardcoding the Compose project
name or compose file path.

```bash
phlo services exec [OPTIONS] SERVICE -- COMMAND [ARGS...]
```

**Options**:

```bash
--tty / --no-tty      # Allocate a TTY (default: no tty)
--backend docker|podman|auto # Container backend for this command
```

**Examples**:

```bash
# Run dbt inside dagster
phlo services exec <service> -- dbt run --select gold.*

# Open an interactive psql shell in postgres
phlo services exec --tty postgres -- psql -U postgres

# Run through Podman
phlo services exec --backend podman trino -- trino --execute "SELECT 1"
```

### phlo services stop

Stop all running services.

```bash
phlo services stop [OPTIONS]
```

**Options**:

```bash
--volumes, -v        # Remove volumes (deletes all data)
--profile PROFILE    # Stop only services in specified profile
--service SERVICE    # Stop specific service(s)
--native             # Also stop native subprocess services
--backend docker|podman|auto # Container backend for this command
```

**Examples**:

```bash
# Stop all services (preserve data)
phlo services stop

# Stop and delete all data
phlo services stop --volumes

# Stop specific profile
phlo services stop --profile observability

# Stop specific services
phlo services stop --service postgres,minio
```

Backend-aware service commands accept `--backend docker|podman|auto` where they
invoke Compose, including `start`, `stop`, `restart`, `logs`, `exec`, `status`, and `reset`.
Runtime inspection commands `list` and `ports` accept the same option for live status.
Docker Compose v2 is the currently supported path; Podman is documented as experimental
and is not CI-supported.

```bash
phlo services logs --backend podman
phlo services ports --backend podman --json
```

### phlo services list

List available services with status.

```bash
phlo services list [OPTIONS]
```

**Options**:

```bash
--all                # Show all services including optional
--json               # Output as JSON
```

**Examples**:

```bash
phlo services list
phlo services list --all
phlo services list --json
```

**Error behavior**:

- Invalid `phlo.yaml` is surfaced with a targeted message:

```bash
phlo services list --json
# Error: Failed to read /path/to/phlo.yaml. Check YAML syntax and file permissions, then retry.
```

- Discovery failures include next-step diagnostics:

```bash
phlo services list --json
# Error: Failed to discover services. Verify service plugins are installed and run `phlo plugin list` for diagnostics.
```

### phlo services ports

Show port mappings for all services.

```bash
phlo services ports [OPTIONS]
```

**Options**:

```bash
--json               # Output as JSON
--all                # Include stopped services with defaults
```

**Source values**:

- `env`: Port set via compose env resolution, including shell vars, `phlo.yaml` `env:`, or `.phlo/.env*`
- `compose`: Literal host port from the compose mapping
- `default`: Using the service.yaml default
- `runtime`: Live port detected from the running container

**Examples**:

```bash
phlo services ports
phlo services ports --json
phlo services ports --all
```

**Output**:

```
Service            Host Port   Container Port   Source     Status
----------------------------------------------------------------------
postgres           10000       5432             env       Running
minio-api          10001       9000             env       Running
dagster-webserver  10006       3000             env       Running
clickhouse-http    8123        8123             default   Stopped
```

**Conflict detection**:

When two services resolve to the same host port, both rows are highlighted with a `⚠` prefix:

```bash
⚠ Port conflict: trino and hasura both map to host port 8080
```

### phlo services add

Add an optional service to the project.

```bash
phlo services add SERVICE_NAME [OPTIONS]
```

**Options**:

```bash
--no-start           # Don't start the service after adding
```

**Examples**:

```bash
phlo services add prometheus
phlo services add grafana --no-start
```

**State semantics**:

- `add` ensures the service is present in `services.enabled`.
- `add` removes that service from `services.disabled` if present.
- If already enabled and not disabled, command exits without changing config.

```yaml
# Before
services:
  enabled: []
  disabled:
    - prometheus
```

```bash
phlo services add prometheus --no-start
```

```yaml
# After
services:
  enabled:
    - prometheus
  disabled: []
```

### phlo services remove

Remove a service from the project.

```bash
phlo services remove SERVICE_NAME [OPTIONS]
```

**Options**:

```bash
--keep-running       # Don't stop the service
```

**Examples**:

```bash
phlo services remove prometheus
phlo services remove grafana --keep-running
```

**State semantics**:

- `remove` removes the service from `services.enabled`.
- `remove` adds the service to `services.disabled`.
- By default it stops the service first; `--keep-running` skips the stop step.

```yaml
# Before
services:
  enabled:
    - prometheus
  disabled: []
```

```bash
phlo services remove prometheus --keep-running
```

```yaml
# After
services:
  enabled: []
  disabled:
    - prometheus
```

### phlo services reset

Reset infrastructure by stopping services and deleting volumes.

```bash
phlo services reset [OPTIONS]
```

**Options**:

```bash
--service SERVICE    # Reset only specific service(s)
-y, --yes            # Skip confirmation
```

**Examples**:

```bash
phlo services reset
phlo services reset --service postgres
phlo services reset -y
```

### phlo services restart

Restart services (stop + start).

```bash
phlo services restart [OPTIONS]
```

**Options**:

```bash
--build              # Rebuild containers before starting
--profile PROFILE    # Restart services in profile
--service SERVICE    # Restart specific service(s)
--dev                # Enable dev mode when restarting
```

**Examples**:

```bash
phlo services restart
phlo services restart --build
phlo services restart --service dagster
```

### phlo services status

Show status of all services.

```bash
phlo services status
```

**Output**:

```
SERVICE              STATUS    PORTS
postgres             running   10000
minio                running   10001-10002
nessie               running   10003
trino                running   10005
dagster-webserver    running   10006
dagster-daemon       running
```

### phlo services logs

View service logs.

```bash
phlo services logs [OPTIONS] [SERVICE]
```

**Options**:

```bash
--follow, -f         # Follow log output
--tail N             # Show last N lines
```

**Examples**:

```bash
# All logs
phlo services logs

# Follow specific service
phlo services logs -f dagster-webserver

# Last 100 lines
phlo services logs --tail 100 trino
```

## Plugin Commands

Manage Phlo plugins for extending functionality.

### phlo plugin list

List all installed plugins.

```bash
phlo plugin list [OPTIONS]
```

**Options**:

```bash
--type TYPE          # Filter by plugin type (sources, quality, transforms, services, hooks, assets, resources, orchestrators, catalogs, all)
--all                # Include registry plugins in output
--json               # Output as JSON
```

**Examples**:

```bash
# List all plugins
phlo plugin list

# List only source connectors
phlo plugin list --type sources

# List hook plugins
phlo plugin list --type hooks

# JSON output
phlo plugin list --json
```

**Output**:

```
Services:
  NAME              VERSION    DESCRIPTION
  dagster           0.1.0      Dagster orchestration engine
  postgres          0.1.0      PostgreSQL database
  trino             0.1.0      Distributed SQL query engine

Sources:
  NAME              VERSION    DESCRIPTION
  rest_api          1.0.0      REST API connector
  jsonplaceholder   1.0.0      JSONPlaceholder example source

Quality Checks:
  NAME              VERSION    DESCRIPTION
  null_check        1.0.0      Null value validation
  threshold_check   1.0.0      Threshold validation
```

### phlo plugin search

Search available plugins in the registry.

```bash
phlo plugin search [QUERY] [OPTIONS]
```

**Options**:

```bash
--type TYPE          # Filter by plugin type
--json               # Output as JSON
--tag TAG            # Filter by tag
```

**Examples**:

```bash
# Search for PostgreSQL-related plugins
phlo plugin search postgres

# Search for quality check plugins
phlo plugin search --type quality

# Search for hook plugins
phlo plugin search --type hooks

# Search by tag
phlo plugin search --tag observability
```

### phlo plugin install

Install a plugin from the registry.

```bash
phlo plugin install PLUGIN_NAME [OPTIONS]
```

**Options**: none

**Examples**:

```bash
# Install a plugin
phlo plugin install phlo-superset

# Install specific version
phlo plugin install phlo-superset==0.2.5
```

### phlo plugin info

Show detailed information about a plugin.

```bash
phlo plugin info PLUGIN_NAME [OPTIONS]
```

**Options**:

```bash
--type TYPE          # Plugin type (auto-detected if omitted)
--json               # Output as JSON
```

**Examples**:

```bash
# Get plugin info (auto-detect type)
phlo plugin info dagster

# Specify type
phlo plugin info rest_api --type sources

# JSON output
phlo plugin info dagster --json
```

**Output**:

```
dagster
Type: services
Version: 0.1.0
Author: Phlo Team
Description: Dagster orchestration engine for workflow management
License: MIT
Homepage: https://github.com/iamgp/phlo
Tags: orchestration, core, service

Service Details:
  Container: dagster-webserver
  Ports: 10006
  Dependencies: postgres
```

### phlo plugin update

Update plugins to latest versions.

```bash
phlo plugin update [OPTIONS]
```

**Options**:

```bash
--dry-run            # Show available updates without applying
--json               # Output available updates as JSON
```

**Examples**:

```bash
# Update available plugins
phlo plugin update

# Check for updates without installing
phlo plugin update --dry-run

# JSON output
phlo plugin update --json
```

### phlo plugin create

Create a new plugin scaffold (for plugin development).

```bash
phlo plugin create PLUGIN_NAME --type TYPE [OPTIONS]
```

**Options**:

```bash
--type TYPE          # Plugin type: sources, quality, transforms, services, hooks, catalogs, assets, resources, orchestrators
--path PATH          # Custom output path
```

**Examples**:

```bash
# Create source connector plugin
phlo plugin create my-api-source --type source

# Create quality check plugin
phlo plugin create my-validation --type quality

# Create hook plugin
phlo plugin create my-hooks --type hook

# Create with custom path
phlo plugin create my-plugin --type source --path ./plugins/my-plugin
```

**Creates**:

```
phlo-plugin-my-api-source/
├── pyproject.toml           # Package config with entry points
├── README.md                # Documentation
├── src/
│   └── phlo_my_api_source/
│       ├── __init__.py
│       └── plugin.py        # Plugin implementation
└── tests/
    └── test_plugin.py       # Test suite
```

### phlo plugin check

Validate installed plugins.

```bash
phlo plugin check [OPTIONS]
```

**Options**:

```bash
--json               # Output as JSON
```

**Examples**:

```bash
# Validate all plugins
phlo plugin check

# JSON output
phlo plugin check --json
```

## Project Commands

### phlo init

Initialize a new Phlo project.

```bash
phlo init [PROJECT_NAME] [OPTIONS]
```

**Options**:

```bash
--template TEMPLATE      # Project template (default: minimal)
--list-templates         # List available project templates and exit
--force                  # Initialize in non-empty directory
```

**Templates**:

- `minimal`: Minimal project structure (no transforms)
- `basic`: dbt-ready project (requires `phlo-dbt`)
- `csv-batch`: Local CSV batch pipeline with partition-aware sample event keys
- `api-ingestion`: REST API ingestion pipeline with partition-aware sample event keys
- `dbt-medallion`: Bronze/silver/gold dbt project
- `sling-replication`: Sling replication starter
- `observability-demo`: Pipeline with telemetry wiring and partition-aware sample event keys

#### List templates

```bash
phlo init --list-templates
```

#### Create from a template

```bash
phlo init my-project --template csv-batch
```

#### Create the default minimal project

```bash
phlo init my-project
```

**Example**:

```bash
phlo init my-lakehouse --template basic
cd my-lakehouse
```

**Creates**:

```
my-lakehouse/
├── .env.example          # Local secrets template (.phlo/.env.local)
├── workflows/
│   ├── ingestion/
│   ├── schemas/
│   └── transforms/       # dbt templates only
│       └── dbt/
├── tests/
└── phlo.yaml
```

### phlo dev

Start Dagster development server.

```bash
phlo dev [OPTIONS]
```

**Options**:

```bash
--port PORT          # Port for webserver (default: 3000)
--host HOST          # Host to bind (default: 127.0.0.1)
--workflows-path PATH # Path to workflows directory
```

**Example**:

```bash
phlo dev --port 3000
```

Opens Dagster UI at http://localhost:3000

## Workflow Commands

### phlo workflow create

Interactive workflow creation wizard.

```bash
phlo workflow create [OPTIONS]
```

**Options**:

```bash
--type TYPE          # Workflow type (currently: ingestion)
--domain DOMAIN      # Domain/namespace (e.g., api, files)
--table TABLE        # Table name
--unique-key KEY     # Unique key column
--cron CRON          # Cron schedule expression
--api-base-url URL   # REST API base URL (optional)
--field NAME:TYPE    # Additional schema field (repeatable)
--json               # Emit machine-readable JSON envelope
```

**Interactive prompts**:

1. Workflow type (ingestion/quality/transform)
2. Domain name
3. Table name
4. Unique key column
5. Schedule (cron expression)
6. API base URL (optional)

**Example (interactive)**:

```bash
phlo workflow create
```

**Example (non-interactive)**:

```bash
phlo workflow create \
  --type ingestion \
  --domain github \
  --table events \
  --unique-key id
```

**Creates**:

```
workflows/
├── ingestion/
│   └── github/
│       └── events.py
└── schemas/
    └── github.py
```

### phlo workflow check

Validate a workflow file and its inferred schema file.

```bash
phlo workflow check workflows/ingestion/weather/observations.py
```

## Asset Commands

### phlo materialize

Materialize Dagster assets.

```bash
phlo materialize ASSET_NAME [OPTIONS]
```

When running through Docker, or through the experimental non-CI-supported Podman backend, Phlo waits for the Dagster runtime to finish
startup and local package installation before executing the materialization. If the run
fails, normal terminal output stays concise; use `phlo logs --level ERROR --limit 20`
for the structured failure details.

**Options**:

```bash
--select SELECTOR        # Asset selection query
--partition PARTITION    # Specific partition to materialize
--no-contract-refresh    # Skip automatic schema-contract refresh
--dry-run                # Show command without executing
```

**Write-audit-publish (WAP) launch**:

WAP is a project policy configured in `phlo.yaml`, not a command-line option.
When enabled, every `phlo materialize ASSET_NAME` creates a fresh
`pipeline-run-<logical-run-id>` catalog branch before asking Dagster GraphQL to
start the asset. Phlo applies the `phlo/wap_branch` and `phlo/ref` tags to bind
the run to that immutable ref. A configured `PHLO_DAGSTER_ACCESS_TOKEN` is sent
as a bearer token but never appears in process arguments or shell history.
For a remote endpoint, the token and HTTPS are required; plaintext HTTP is
accepted only for the default local endpoint.

```bash
phlo materialize dlt_events
```

Before GraphQL submission, Phlo writes an immutable launch manifest that binds
the logical run ID, branch, tags, and catalog hashes. If Dagster rejects a
launch, the run fails checks, or transport is ambiguous, Phlo retains that
branch and report for audit. With WAP disabled, this command retains its legacy
direct `dagster asset materialize` behavior.

**Selection Syntax**:

```bash
asset_name               # Single asset
asset_name+              # Asset and downstream
+asset_name              # Asset and upstream
asset_name*              # Asset and all dependencies
tag:group_name           # All assets with tag
*                        # All assets
```

**Examples**:

```bash
# Single asset
phlo materialize dlt_events

# Asset and downstream
phlo materialize dlt_events+

# Specific partition
phlo materialize dlt_events --partition 2026-05-04

# By tag
phlo materialize --select "tag:csv"

# Dry run
phlo materialize dlt_events --dry-run

# Skip automatic contract refresh
phlo materialize dlt_events --no-contract-refresh
```

## Testing Commands

## Schema Migration Commands

### phlo schema-migrate export-contract

Export a Phlo contract snapshot for a table.

```bash
phlo schema-migrate export-contract TABLE_NAME [OPTIONS]
```

**Options**:

```bash
--schema-class NAME      # Explicit Pandera schema class name
--output PATH            # Custom contract output path
--force                  # Overwrite existing output file
```

**Examples**:

```bash
phlo schema-migrate export-contract warehouse.customers
phlo schema-migrate export-contract warehouse.customers --output .phlo/contracts/customers.json
```

### phlo schema-migrate scaffold-yaml

Generate migration scaffold YAML from a previously exported contract.

```bash
phlo schema-migrate scaffold-yaml TABLE_NAME [OPTIONS]
```

**Options**:

```bash
--from-contract PATH     # Contract path (default: .phlo/contracts/<table>.json)
--output PATH            # YAML output path (default: .phlo/migrations/<table>.yaml)
--force                  # Overwrite existing output file
```

**Examples**:

```bash
phlo schema-migrate scaffold-yaml warehouse.customers
phlo schema-migrate scaffold-yaml warehouse.customers --from-contract .phlo/contracts/customers.json
```

### phlo schema-migrate scaffold-yaml-recent

Generate migration scaffold YAML files for recently added/updated contracts.

```bash
phlo schema-migrate scaffold-yaml-recent [OPTIONS]
```

**Options**:

```bash
--since-hours INTEGER    # Only include contracts modified in last N hours (default: 24)
--limit INTEGER          # Max recent contracts to scaffold
--force                  # Overwrite existing output files
```

**Examples**:

```bash
phlo schema-migrate scaffold-yaml-recent
phlo schema-migrate scaffold-yaml-recent --since-hours 1 --limit 5
```

### phlo schema-migrate plan/apply/history

Existing migration lifecycle commands remain available:

Schema migrator selection order:
- explicit `schema_migrator` capability default
- migrator matching the configured default `table_store`
- the only installed schema migrator, when exactly one exists

If multiple schema migrators are installed and neither default selects one, Phlo fails and asks for explicit configuration instead of choosing the first registered provider.

```bash
phlo schema-migrate plan warehouse.customers
phlo schema-migrate apply warehouse.customers
phlo schema-migrate history warehouse.customers --limit 5
```

## Data Migration Commands

For end-to-end spec authoring and execution workflow, see
[Data Migrations Guide](../guides/data-migrations.md).

### phlo migrate decorators-2026-05

Migrate legacy Phlo decorator usage to the May 2026 API style.

```bash
phlo migrate decorators-2026-05 PATH [OPTIONS]
```

This codemod rewrites supported legacy usage such as `@phlo_ingestion(...)`,
`@phlo.ingestion(...)`, and `@phlo_quality(...)` to the explicit May 2026
decorator calls, such as `@phlo.ingest.dlt(...)` and `@phlo.quality.pandera(...)`.

**Options**:

```bash
--check                 # Fail if migrations are needed
--write                 # Rewrite files in place
--diff                  # Print unified diffs for pending changes
```

### phlo migrate validate

Validate a migration YAML spec without executing writes.

```bash
phlo migrate validate SPEC_FILE
```

### phlo migrate run

Run a migration YAML spec.

```bash
phlo migrate run SPEC_FILE [OPTIONS]
```

**Options**:

```bash
--dry-run               # Read and validate without writes
--format table|json     # Output format (default: table)
```

### phlo migrate list

List migration specs from default directories (`migrations/`, `workflows/migrations/`).

```bash
phlo migrate list [--directory PATH]
```

### phlo migrate status

Show recent migration run history.

```bash
phlo migrate status [OPTIONS]
```

**Options**:

```bash
--limit INTEGER         # Number of entries to show (default: 10)
--format table|json     # Output format (default: table)
```

### phlo test

Run tests.

```bash
phlo test [TEST_PATH] [OPTIONS]
```

**Options**:

```bash
--local              # Skip Docker integration tests
--verbose, -v        # Verbose output
--marker, -m MARKER  # Run tests with marker
--coverage           # Generate coverage report
```

**Markers**:

- `integration`: Integration tests requiring Docker
- `unit`: Fast unit tests
- `slow`: Slow-running tests

**Examples**:

```bash
# All tests
phlo test

# Specific test file
phlo test tests/test_ingestion.py

# Unit tests only
phlo test -m unit

# Skip integration tests
phlo test --local

# Specific test
phlo test -k test_events_ingestion

# With coverage
phlo test --coverage
```

## Logging & Monitoring Commands

### phlo logs

Query and filter structured logs from Dagster runs.

```bash
phlo logs [OPTIONS]
```

Error-level filters search a wider run-event window and prefer richer Dagster event-log
details when available, so nested materialization failures show the actionable root cause
rather than only the top-level Dagster wrapper.

**Options**:

```bash
--asset NAME         # Filter by asset name
--job NAME           # Filter by job name
--level LEVEL        # Filter by log level: DEBUG, INFO, WARNING, ERROR
--since TIME         # Filter by time (e.g., 1h, 30m, 2d)
--run-id ID          # Get logs for specific run
--follow, -f         # Tail mode - follow new logs in real-time
--full               # Don't truncate long messages
--limit N            # Number of logs to retrieve (default: 100)
--json               # JSON output for scripting
```

**Examples**:

```bash
# View recent logs
phlo logs

# Filter by asset
phlo logs --asset dlt_events

# Filter by log level
phlo logs --level ERROR

# Logs from last hour
phlo logs --since 1h

# Follow logs in real-time
phlo logs --follow

# Logs for specific run
phlo logs --run-id abc-123-def

# JSON output for processing
phlo logs --json --limit 500
```

### phlo backfill

Backfill partitioned assets over a date range.

```bash
phlo backfill [ASSET_NAME] [OPTIONS]
```

Backfills use the same container runtime readiness checks as `phlo materialize`, so a
freshly started Dagster container does not race local package installation before
partition work begins.

**Arguments**:

- `ASSET_NAME` (optional): Asset to backfill

**Options**:

```bash
--start-date DATE    # Start date (YYYY-MM-DD)
--end-date DATE      # End date (YYYY-MM-DD)
--partitions DATES   # Comma-separated partition dates (YYYY-MM-DD,...)
--parallel N         # Number of concurrent partitions (default: 1)
--resume             # Resume last backfill, skipping completed partitions
--dry-run            # Show what would be executed without running
--delay SECS         # Delay between parallel executions in seconds (default: 0.0)
```

**Examples**:

```bash
# Backfill for date range
phlo backfill dlt_events --start-date 2026-05-01 --end-date 2026-05-07

# Specific partitions
phlo backfill dlt_events --partitions 2026-05-01,2026-05-02,2026-05-03

# Parallel backfill (5 concurrent)
phlo backfill dlt_events --start-date 2026-05-01 --end-date 2026-05-07 --parallel 5

# Resume interrupted backfill
phlo backfill dlt_events --resume

# Dry run to see what would execute
phlo backfill dlt_events --start-date 2026-05-01 --end-date 2026-05-07 --dry-run

# With delay between executions
phlo backfill dlt_events --start-date 2026-05-01 --end-date 2026-05-07 --parallel 3 --delay 2.5
```

## Branch Commands

Manage Nessie catalog branches.

### phlo branch create

Create a new branch.

```bash
phlo branch create BRANCH_NAME [OPTIONS]
```

**Options**:

```bash
--from REF           # Create from reference (default: main)
--description DESC   # Branch description
```

**Examples**:

```bash
# Create from main
phlo branch create dev

# Create from specific commit
phlo branch create feature --from abc123

# With description
phlo branch create dev --description "Development branch"
```

### phlo branch list

List all branches.

```bash
phlo branch list [OPTIONS]
```

**Options**:

```bash
--pattern PATTERN    # Filter by pattern
--show-hashes        # Show commit hashes
```

**Example**:

```bash
phlo branch list
phlo branch list --pattern "pipeline/*"
```

### phlo branch merge

Merge branches.

```bash
phlo branch merge SOURCE TARGET [OPTIONS]
```

**Options**:

```bash
--strategy STRATEGY  # Merge strategy (default: normal)
--no-ff              # Create merge commit even if fast-forward
```

**Examples**:

```bash
# Merge dev to main
phlo branch merge dev main

# Force merge commit
phlo branch merge dev main --no-ff
```

### phlo branch delete

Delete a branch.

```bash
phlo branch delete BRANCH_NAME [OPTIONS]
```

**Options**:

```bash
--force, -f          # Force delete even if not merged
```

**Examples**:

```bash
phlo branch delete old-feature
phlo branch delete old-feature --force
```

### phlo branch diff

Show differences between two branches.

```bash
phlo branch diff SOURCE_BRANCH [TARGET_BRANCH] [OPTIONS]
```

**Arguments**:

- `SOURCE_BRANCH`: Source branch to compare
- `TARGET_BRANCH`: Target branch (default: main)

**Options**:

```bash
--format FORMAT      # Output format: table, json (default: table)
```

**Examples**:

```bash
# Compare dev to main
phlo branch diff dev

# Compare two branches
phlo branch diff feature-a feature-b

# JSON output
phlo branch diff dev main --format json
```

## Catalog Commands

Manage the Iceberg catalog (Nessie-backed).

### phlo catalog tables

List all Iceberg tables in the catalog.

```bash
phlo catalog tables [OPTIONS]
```

**Options**:

```bash
--namespace NS       # Filter by namespace (e.g., bronze, silver)
--ref REF            # Nessie branch/tag reference (default: main)
--format FORMAT      # Output format: table, json (default: table)
```

**Examples**:

```bash
# List all tables
phlo catalog tables

# List tables in specific namespace
phlo catalog tables --namespace bronze

# List tables on specific branch
phlo catalog tables --ref feature-branch

# JSON output
phlo catalog tables --format json
```

### phlo catalog describe

Show detailed table metadata.

```bash
phlo catalog describe TABLE_NAME [OPTIONS]
```

**Arguments**:

- `TABLE_NAME`: Table to describe (e.g., `bronze.events`)

**Options**:

```bash
--ref REF            # Nessie branch/tag reference (default: main)
```

**Examples**:

```bash
# Describe table
phlo catalog describe bronze.events

# Describe on specific branch
phlo catalog describe bronze.events --ref dev
```

**Output**:

Shows table metadata including:

- Location
- Current snapshot ID
- Format version
- Schema (columns, types, constraints)
- Partitioning
- Properties

### phlo catalog history

Show table snapshot history.

```bash
phlo catalog history TABLE_NAME [OPTIONS]
```

**Arguments**:

- `TABLE_NAME`: Table name

**Options**:

```bash
--limit N            # Number of snapshots to show (default: 10)
--ref REF            # Nessie branch/tag reference (default: main)
```

**Examples**:

```bash
# Show recent snapshots
phlo catalog history bronze.events

# Show last 20 snapshots
phlo catalog history bronze.events --limit 20
```

### phlo lineage show

Display asset lineage in ASCII tree format.

```bash
phlo lineage show ASSET_NAME [OPTIONS]
```

**Arguments**:

- `ASSET_NAME`: Asset name to show lineage for

**Options**:

```bash
--direction DIR      # Direction: upstream, downstream, both (default: both)
--depth N            # Maximum depth to traverse
```

**Examples**:

```bash
# Show full lineage
phlo lineage show dlt_events

# Show only upstream dependencies
phlo lineage show dlt_events --direction upstream

# Limit depth
phlo lineage show dlt_events --depth 2
```

### phlo lineage export

Export lineage to external formats.

```bash
phlo lineage export ASSET_NAME [OPTIONS]
```

**Arguments**:

- `ASSET_NAME`: Asset name to export lineage for

**Options**:

```bash
--format FORMAT      # Export format: dot, mermaid, json (default: dot)
--output PATH        # Output file path (required)
```

**Examples**:

```bash
# Export to Graphviz DOT
phlo lineage export dlt_events --format dot --output lineage.dot

# Export to Mermaid diagram
phlo lineage export dlt_events --format mermaid --output lineage.md

# Export to JSON
phlo lineage export dlt_events --format json --output lineage.json
```

## Configuration Commands

### phlo config show

Display current configuration.

```bash
phlo config show [OPTIONS]
```

**Options**:

```bash
--format FORMAT      # Output format: yaml, json, env
--secrets            # Show secrets (masked by default)
```

**Examples**:

```bash
phlo config show
phlo config show --format json
phlo config show --secrets
```

### phlo config validate

Validate configuration files.

```bash
phlo config validate [FILE]
```

**Examples**:

```bash
# Validate phlo.yaml
phlo config validate phlo.yaml
```

### phlo env export

Export the generated environment configuration.

```bash
phlo env export [OPTIONS]
```

**Examples**:

```bash
# Export non-secret defaults
phlo env export

# Export with secrets (from .phlo/.env.local)
phlo env export --include-secrets

# Write to a file
phlo env export --include-secrets --output env.full
```

## Utility Commands

### phlo status

Show asset status and freshness.

Asset `never_run`/`stale` states are derived only from wired evidence
sources. Components whose evidence source is not wired are shown as
`unknown` (`—`), never as a stub-derived state.

```bash
phlo status [OPTIONS]
```

**Options**:

```bash
--assets             # Show assets only
--services           # Show services only
--stale              # Show only stale assets
--group GROUP        # Filter by group
--json               # JSON output for scripting
```

**Example**:

```bash
phlo status
phlo status --stale
phlo status --group csv
```

### phlo validate-schema

Validate Pandera schemas.

```bash
phlo validate-schema SCHEMA_PATH [OPTIONS]
```

**Options**:

```bash
--check-constraints  # Check that constraints are defined
--check-descriptions # Check that field descriptions are present
```

**Example**:

```bash
phlo validate-schema workflows/schemas/events.py
phlo validate-schema workflows/schemas/events.py --no-check-descriptions
```

### phlo validate-workflow

Validate workflow configuration.

```bash
phlo validate-workflow WORKFLOW_PATH [OPTIONS]
```

**Arguments**:

- `WORKFLOW_PATH`: Path to workflow file or directory

**Options**:

```bash
--fix                # Auto-fix issues where possible
```

**Examples**:

```bash
# Validate single workflow
phlo validate-workflow workflows/ingestion/api/events.py

# Validate directory
phlo validate-workflow workflows/ingestion/

# Auto-fix where possible
phlo validate-workflow workflows/ingestion/weather.py --fix
```

### phlo schema

Manage Pandera schemas.

```bash
phlo schema COMMAND [OPTIONS]
```

**Subcommands**:

#### list

List all available Pandera schemas.

```bash
phlo schema list [OPTIONS]
```

**Options**:

```bash
--domain DOMAIN      # Filter by domain
--format FORMAT      # Output format: table, json (default: table)
```

**Examples**:

```bash
# List all schemas
phlo schema list

# Filter by domain
phlo schema list --domain api

# JSON output
phlo schema list --format json
```

## Optional Plugin Commands

The following commands are provided by optional packages. Install the corresponding package to use these commands.

### phlo postgrest

PostgREST configuration and management.

**Installation**: `phlo plugin install phlo-postgrest`

### phlo hasura

Hasura GraphQL engine management.

**Installation**: `phlo plugin install phlo-hasura`

### phlo dbt publishing

dbt publishing layer management.

**Installation**: `phlo plugin install phlo-dbt`

### phlo metrics

Metrics summary and export. Built into core Phlo.

```bash
phlo metrics summary --json
phlo metrics asset my_asset --json
phlo metrics export --format json --output metrics.json --json
```

### phlo mcp

MCP server management and introspection.

```bash
phlo mcp serve --transport stdio
phlo mcp install amp --dry-run --json
phlo mcp tools --json
phlo mcp prompts --json
phlo mcp config --json
```

### phlo audit

Inspect local JSONL audit records written by MCP and API mutation surfaces.

```bash
phlo audit tail --since 1h --json
phlo audit query --operation materialize_asset --json
```

### phlo alerts

Alerting rules and notifications.

**Installation**: `phlo plugin install phlo-alerting`

### phlo openmetadata

OpenMetadata catalog integration.

**Installation**: `phlo plugin install phlo-openmetadata`

Run `phlo <command> --help` after installation for command-specific documentation.

## Environment Variables

CLI behavior can be customized with environment variables:

```bash
# Dagster home directory
export DAGSTER_HOME=~/.dagster

# Workspace YAML location
export DAGSTER_WORKSPACE=/path/to/workspace.yaml

# Phlo configuration
export PHLO_CONFIG=/path/to/phlo.yaml

# Log level
export PHLO_LOG_LEVEL=DEBUG
```

## Exit Codes

```bash
0    # Success
1    # General error
2    # Command not found
3    # Invalid arguments
4    # Configuration error
5    # Service error
```

## Examples Cookbook

### Complete Workflow Setup

```bash
# 1. Create project
phlo init my-project
cd my-project

# 2. Initialize infrastructure
phlo services init

# 3. Start services
phlo services start

# 4. Create workflow
phlo workflow create

# 5. Run tests
phlo test

# 6. Materialize
phlo materialize dlt_events
```

### Development Workflow

```bash
# Start Observatory/phlo-api natively (no Docker)
phlo services start --native

# Create feature branch
phlo branch create feature-new-workflow

# Create workflow
phlo workflow create

# Test workflow
phlo test events

# Materialize to test
phlo materialize dlt_events --partition 2025-01-15

# Merge to main
phlo branch merge feature-new-workflow main
```

### Troubleshooting Workflow

```bash
# Check service status
phlo services status

# View logs
phlo services logs -f dagster-webserver

# Check stale assets
phlo status --stale

# Validate configuration
phlo config validate

# Re-materialize failed asset
phlo materialize failed_asset
```

## Next Steps

- [Configuration Reference](configuration-reference.md) - Detailed configuration options
- [Developer Guide](../guides/developer-guide.md) - Building workflows
- [Troubleshooting](../operations/troubleshooting.md) - Common issues
