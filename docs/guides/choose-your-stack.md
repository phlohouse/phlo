# Choose your stack

This guide selects capability providers, installs the matching packages, and regenerates the project services from a declared stack.

## Before you start

- You have a project created with `phlo init` and can edit `phlo.yaml`.
- You know whether the project needs the default Iceberg lakehouse or another installed provider.
- You can install packages into the same environment that runs the `phlo` command.

## 1. Start from a template

List the templates provided by the installed packages, then choose the closest starting point:

```bash
phlo init --help
phlo init demo --template dbt-medallion
```

The current template set includes `minimal`, `basic`, `dbt-medallion`, `csv-batch`, `api-ingestion`, `observability-demo`, and `sling-replication`. The initializer creates the workflow tree and the service configuration that the template declares.

## 2. Understand capability names

Providers register against neutral capability types rather than making asset code import a concrete implementation. The registry currently uses names including `table_store`, `catalog`, `object_store`, `query_engine`, `orchestrator`, `transformation`, `ingestion_provider`, `quality_provider`, `schema_migrator`, and `observability`.

| Capability | Default provider | Package |
| --- | --- | --- |
| `orchestrator` | `dagster` | `phlo-dagster` |
| `table_store` | `iceberg` | `phlo-iceberg` |
| `catalog` | `nessie` | `phlo-nessie` |
| `object_store` | `minio` | `phlo-minio` |
| `query_engine` | `trino` | `phlo-trino` |
| `ingestion_provider` | `dlt` | `phlo-dlt` |
| `quality_provider` | `pandera` | `phlo-pandera` |
| `transformation` | `dbt` | `phlo-dbt` |

## 3. Install a provider set

Install the packages for the capabilities used by the workflows:

```bash
uv pip install phlo-dagster phlo-dlt phlo-pandera phlo-iceberg phlo-nessie phlo-minio phlo-trino
```

Installation makes the entry points discoverable. It does not change the generated Compose file until services are initialized again.

## 4. Select a non-default provider

Use `PHLO_DEFAULT_CAPABILITIES` for project-wide provider selection when more than one package supplies a capability:

```bash
export PHLO_DEFAULT_CAPABILITIES='{"table_store":"iceberg","catalog":"nessie","query_engine":"trino"}'
```

The value is a JSON mapping from capability type to provider name. Asset-level `capabilities={...}` overrides can narrow the choice for one ingestion asset.

## 5. Regenerate infrastructure

Declare service overrides in `phlo.yaml`, then regenerate `.phlo/`:

```yaml
infrastructure:
  services:
    observatory:
      enabled: true
    postgrest:
      enabled: false
```

```bash
phlo services init
phlo services list
phlo services start
```

`services init` renders the installed service definitions and overrides into `.phlo/`. `services list` shows which service plugins are available before Docker starts.

## Verify

```bash
phlo doctor
phlo services status
```

The doctor report shows discovered providers and the status table shows only the enabled services as running or healthy. A capability conflict is an installation or default-selection problem, not a workflow-code problem.

## Related

- [Ingest data](ingest-data.md) for capability overrides on an asset.
- [Expose data](expose-data.md) for optional API and BI providers.
- [Packages](../reference/packages.md) for package support and contribution details.
