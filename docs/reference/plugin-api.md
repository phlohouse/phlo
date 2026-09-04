# Plugin API Reference

Comprehensive API reference for all Phlo plugin base classes.

## Overview

Phlo's plugin system uses abstract base classes to define contracts for each extension point. All plugin types inherit from [`Plugin`](#plugin) and must provide [`PluginMetadata`](#pluginmetadata).

```
Plugin (ABC)
├── ServicePlugin
├── CliCommandPlugin
├── SourceConnectorPlugin
├── QualityCheckPlugin[T]
├── TransformationPlugin
├── CatalogPlugin
├── AssetProviderPlugin
├── ResourceProviderPlugin
└── OrchestratorAdapterPlugin
```

**Import path** — all base classes are re-exported from a single module:

```python
from phlo.plugins.base import (
    Plugin,
    PluginMetadata,
    ServicePlugin,
    CliCommandPlugin,
    SourceConnectorPlugin,
    QualityCheckPlugin,
    TransformationPlugin,
    CatalogPlugin,
    AssetProviderPlugin,
    ResourceProviderPlugin,
    OrchestratorAdapterPlugin,
)
```

---

## Plugin

`phlo.plugins.base.plugin.Plugin`

Abstract base class for all Phlo plugins.

### Abstract properties

| Property | Return type | Description |
|----------|-------------|-------------|
| `metadata` | `PluginMetadata` | Plugin identity and metadata. |

### Concrete methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `initialize` | `(config: dict[str, Any]) -> None` | Called once when the plugin is loaded. Override to validate config, set up connections, or load resources. |
| `cleanup` | `() -> None` | Called when the plugin is unloaded. Override to close connections, release resources, or save state. |

---

## PluginMetadata

`phlo.plugins.base.plugin.PluginMetadata`

Dataclass describing a plugin's identity.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | *(required)* | Unique name within plugin type. |
| `version` | `str` | *(required)* | Semver version string. |
| `description` | `str` | `""` | Human-readable description. |
| `author` | `str` | `""` | Author name or organization. |
| `license` | `str` | `""` | License identifier (e.g., `MIT`, `Apache-2.0`). |
| `homepage` | `str` | `""` | Homepage or repository URL. |
| `tags` | `list[str]` | `[]` | Tags for categorization and search. |
| `dependencies` | `list[str]` | `[]` | Python package dependencies required by the plugin. |

```python
from phlo.plugins.base import PluginMetadata

meta = PluginMetadata(
    name="my-plugin",
    version="0.1.0",
    description="Example plugin",
    author="Acme Corp",
    tags=["example"],
)
```

---

## ServicePlugin

`phlo.plugins.base.service.ServicePlugin`

**Inherits:** `Plugin`, `ABC`

Provides Docker-based infrastructure components that compose into a Phlo stack. The `service_definition` property returns a dict equivalent to a `service.yaml` file.

### Abstract properties

| Property | Return type | Description |
|----------|-------------|-------------|
| `service_definition` | `dict[str, Any]` | Full service definition (equivalent to `service.yaml`). |

### Concrete properties

| Property | Return type | Description |
|----------|-------------|-------------|
| `category` | `str` | Service category (`core`, `api`, `bi`, `observability`, etc.). Defaults to `"custom"`. |
| `is_default` | `bool` | Whether the service is installed by default. |
| `profile` | `str \| None` | Optional profile this service belongs to. |

### Concrete methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_compose_fragment` | `() -> dict[str, Any]` | Docker Compose service configuration from definition. |
| `get_files` | `() -> list[dict[str, str]]` | Files to copy during initialization. |
| `get_dependencies` | `() -> list[str]` | Service names this service depends on. |

### Example

```python
from phlo.plugins.base import ServicePlugin, PluginMetadata

class RedisService(ServicePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="redis", version="1.0.0", description="Redis cache")

    @property
    def service_definition(self) -> dict:
        return {
            "category": "core",
            "default": False,
            "compose": {
                "image": "redis:7-alpine",
                "ports": ["6379:6379"],
            },
        }
```

---

## CliCommandPlugin

`phlo.plugins.base.cli.CliCommandPlugin`

**Inherits:** `Plugin`, `ABC`

Contributes [Click](https://click.palletsprojects.com/) commands or groups to the `phlo` CLI at runtime. Keeps the core CLI lightweight while capability packages provide their own CLI surface.

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_cli_commands` | `() -> list[click.Command]` | Return Click commands/groups to register on the root CLI. |

### Example

```python
import click
from phlo.plugins.base import CliCommandPlugin, PluginMetadata

class MyCliPlugin(CliCommandPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="hello", version="1.0.0")

    def get_cli_commands(self) -> list[click.Command]:
        @click.command()
        def hello():
            """Say hello from a plugin."""
            click.echo("Hello from plugin!")

        return [hello]
```

---

## SourceConnectorPlugin

`phlo.plugins.base.source.SourceConnectorPlugin`

**Inherits:** `Plugin`, `ABC`

Enables ingesting data from external sources (APIs, databases, file systems, etc.).

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `fetch_data` | `(config: dict[str, Any]) -> Iterator[dict[str, Any]]` | Yield dictionaries representing individual records from the source. `config` contains connection parameters, query/filter settings, pagination, and credentials. |

### Concrete methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_schema` | `(config: dict[str, Any]) -> dict[str, str] \| None` | Return a column-name-to-type mapping (e.g., `{"id": "string"}`). Returns `None` if schema is dynamic. |
| `test_connection` | `(config: dict[str, Any]) -> bool` | Test reachability of the source. Default implementation calls `fetch_data` and tries to read one record. |

### Example

```python
from collections.abc import Iterator
from phlo.plugins.base import SourceConnectorPlugin, PluginMetadata

class GitHubConnector(SourceConnectorPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="github", version="1.0.0", description="Fetch data from GitHub API"
        )

    def fetch_data(self, config: dict) -> Iterator[dict]:
        for event in fetch_github_events(config["api_token"], config["repo"]):
            yield event

    def get_schema(self, config: dict) -> dict:
        return {"id": "string", "type": "string", "created_at": "timestamp"}
```

---

## QualityCheckPlugin

`phlo.plugins.base.quality.QualityCheckPlugin`

**Inherits:** `Plugin`, `ABC`, `Generic[TQualityCheck]`

Factory for custom data quality checks that integrate with the `phlo.quality.pandera(...)` decorator.

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_check` | `(**kwargs) -> TQualityCheck` | Create and return a quality check instance configured with the provided parameters. |

### Example

```python
from phlo.plugins.base import QualityCheckPlugin, PluginMetadata
from phlo_pandera.checks import QualityCheck, QualityCheckResult

class BusinessRuleCheck(QualityCheckPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="business_rule", version="1.0.0", description="Validate business rules"
        )

    def create_check(self, **kwargs) -> QualityCheck:
        return BusinessRuleQualityCheck(rule=kwargs["rule"])
```

---

## IngestionProviderPlugin

`phlo.plugins.base.ingestion_provider.IngestionProviderPlugin`

**Inherits:** `Plugin`, `ABC`

Ingestion providers supply decorator factories and asset registries for engines
such as DLT and Sling.

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_decorator` | `() -> Callable` | Return the provider-native ingestion decorator factory. |
| `get_asset_retriever` | `() -> Callable[[], list[Any]]` | Return registered ingestion assets for this provider. |

Ingestion providers are consumed by `phlo.ingest.provider("<name>")` and may also
receive convenience aliases such as `phlo.ingest.dlt(...)` or
`phlo.ingest.sling(...)`. Provider decorators must register `AssetSpec` objects
with provider-neutral metadata keys: `provider`, `asset_type`, `source_name`,
`table_name`, `write_mode`, `primary_key`, `schema_ref`, and `quality_provider`.

---

## QualityProviderPlugin

`phlo.plugins.base.quality_provider.QualityProviderPlugin`

**Inherits:** `Plugin`, `ABC`

Quality providers supply decorator factories, native check classes, schema
extraction, and optional translation from Phlo's neutral quality vocabulary.

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_decorator` | `() -> Callable` | Return the provider-native quality decorator factory. |
| `get_check_classes` | `() -> dict[str, type]` | Return provider-native check classes keyed by short names. |

### Optional methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_schema_extractor` | `() -> Any \| None` | Return a schema extractor for provider-native schemas. |
| `get_reconciliation_checks` | `() -> dict[str, type] \| None` | Return reconciliation check classes. |
| `build_checks_from_rules` | `(rules: list[Any]) -> list[Any] \| None` | Translate Phlo `QualityRule` descriptors into provider-native checks. |

Quality providers may implement `build_checks_from_rules(rules)` to translate
Phlo `QualityRule` descriptors into provider-native checks. Providers that do
not implement this method can still be used through `phlo.quality.provider(name)`
or provider-specific aliases, but they cannot back `phlo.quality.rules(...)`.

---

## TransformationPlugin

`phlo.plugins.base.transform.TransformationPlugin`

**Inherits:** `Plugin`, `ABC`

> **Deprecated (#837 SP9-DECISION-01):** the legacy transformation SDK family
> has no bundled implementation and no roadmap investment. Subclassing emits a
> `DeprecationWarning`, and the family will be removed in an upcoming release.
> Build asset-provider plugins for new integrations. The family stays
> discoverable, scaffoldable, and importable in the meantime (community tier,
> `legacy_verified`).

Custom data processing steps that compose into data pipelines.

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `transform` | `(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame` | Transform a DataFrame and return the result. |

### Concrete methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_output_schema` | `(input_schema: dict[str, str], config: dict[str, Any]) -> dict[str, str] \| None` | Return the schema of the transformed data. Returns `None` if unknown. |
| `validate_config` | `(config: dict[str, Any]) -> bool` | Validate transformation configuration. Returns `True` by default. |

### Example

```python
import pandas as pd
from phlo.plugins.base import TransformationPlugin, PluginMetadata

class PivotTransform(TransformationPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="pivot", version="1.0.0", description="Pivot table")

    def transform(self, df: pd.DataFrame, config: dict) -> pd.DataFrame:
        return df.pivot_table(
            index=config["index"],
            columns=config["columns"],
            values=config["values"],
            aggfunc=config.get("aggfunc", "mean"),
        )
```

---

## CatalogPlugin

`phlo.plugins.base.catalog.CatalogPlugin`

**Inherits:** `Plugin`, `ABC`

Engine-agnostic catalog configuration. Engine adapters serialize catalog properties into their native formats.

### Abstract properties

| Property | Return type | Description |
|----------|-------------|-------------|
| `targets` | `list[str]` | Engine identifiers that can consume this catalog (e.g., `["trino"]`, `["trino", "spark"]`). |
| `catalog_name` | `str` | Catalog identifier used by the engine. |

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_properties` | `() -> dict[str, Any]` | Return catalog configuration as key-value pairs. |

### Concrete methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `supports_target` | `(target: str) -> bool` | Returns `True` if the catalog supports the given engine target. |

### Example

```python
from phlo.plugins.base import CatalogPlugin, PluginMetadata

class IcebergNessieCatalog(CatalogPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="iceberg-nessie", version="1.0.0")

    @property
    def targets(self) -> list[str]:
        return ["trino"]

    @property
    def catalog_name(self) -> str:
        return "iceberg"

    def get_properties(self) -> dict:
        return {
            "connector.name": "iceberg",
            "iceberg.catalog.type": "nessie",
            "iceberg.catalog.uri": "http://nessie:19120/api/v2",
        }
```

---

## AssetProviderPlugin

`phlo.plugins.base.providers.AssetProviderPlugin`

**Inherits:** `Plugin`, `ABC`

Provides orchestrator-agnostic asset and asset-check specifications.

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_assets` | `() -> Iterable[AssetSpec]` | Return asset specifications exposed by this plugin. |

### Concrete methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_checks` | `() -> Iterable[AssetCheckSpec]` | Return asset check specifications. Returns `[]` by default. |

`AssetSpec` and `AssetCheckSpec` are defined in `phlo.capabilities.specs`.

### Example

```python
from phlo.capabilities.specs import AssetSpec
from phlo.plugins.base import AssetProviderPlugin, PluginMetadata

class MyAssetProvider(AssetProviderPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="my-assets", version="1.0.0")

    def get_assets(self):
        yield AssetSpec(key="my_table", group="bronze", description="Raw data")
```

---

## ResourceProviderPlugin

`phlo.plugins.base.providers.ResourceProviderPlugin`

**Inherits:** `Plugin`, `ABC`

Provides resource specifications that assets and checks can depend on.

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_resources` | `() -> Iterable[ResourceSpec]` | Return resource specifications exposed by this plugin. |

`ResourceSpec` is defined in `phlo.capabilities.specs`.

### Example

```python
from phlo.capabilities.specs import ResourceSpec
from phlo.plugins.base import ResourceProviderPlugin, PluginMetadata

class MyResourceProvider(ResourceProviderPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="my-resources", version="1.0.0")

    def get_resources(self):
        yield ResourceSpec(name="my_db", resource=create_db_resource())
```

---

## OrchestratorAdapterPlugin

`phlo.plugins.base.orchestrator.OrchestratorAdapterPlugin`

**Inherits:** `Plugin`, `ABC`

Translates normalized capability specs into orchestrator-native definitions (e.g., Dagster).

### Abstract methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `build_definitions` | `(*, assets: Iterable[AssetSpec], checks: Iterable[AssetCheckSpec], resources: Iterable[ResourceSpec]) -> Any` | Build orchestrator-native definitions from capability specs. |

All spec types are from `phlo.capabilities.specs`.

### Example

```python
from phlo.plugins.base import OrchestratorAdapterPlugin, PluginMetadata

class CustomOrchestratorAdapter(OrchestratorAdapterPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="custom-orchestrator", version="1.0.0")

    def build_definitions(self, *, assets, checks, resources):
        # Convert specs to orchestrator-native definitions
        return build_native_defs(assets, checks, resources)
```

---

## Runtime Capability Interfaces

`phlo.capabilities.interfaces`

These runtime protocols define the provider contracts resolved by capability
discovery and consumed by ingestion/migration flows.

### ApiBackend

Swappable API and graph-serving backend contract:

- `health_check()`
- `describe()`

Use this for packages such as Hasura or future semantic/API backends. Service
plugins answer how to run the backend; `ApiBackend` answers what backend surface
the rest of Phlo can resolve and expose.

### TableStore

Required methods:

- `ensure_table(...)`
- `append_parquet(...)`
- `merge_parquet(...)`

Optional extended operations:

- `overwrite_parquet(...)`
- `delete_rows(...)`
- `compact(...)`
- `list_snapshots(...)`
- `rollback_to_snapshot(...)`
- `vacuum(...)`

### MaintenanceRetentionStore

Optional plan-first retention capability resolved separately from the base table
store. Providers return an exact plan token and structured evidence for
`expire_snapshots(...)` and `cleanup_orphan_files(...)`; a controller must not
assume that a provider's threshold-only procedure enforces the plan's object or
byte limits.

Maintenance discovery and table statistics are resolved through the neutral
`table_store` capability's optional discovery contract, so Dagster does not
import a provider package to enumerate tables or collect statistics.

### GovernanceBackend

Policy contract for governance providers:

- `list_policies(...)`
- `apply_policy(...)`
- `revoke_policy(...)`
- `check_access(...)`

### SecretBackend

Secret storage contract:

- `get_secret(key)`
- `list_secrets()`

### SchemaExtractor

Provider contract for converting native quality schemas into
`NormalizedSchema`:

- `extract(native_schema)`

### SchemaMigrator

Storage-native schema migration contract:

- `supported_changes()`
- `classify_change(change_type, **details)`
- `diff_schema(table_name, desired)`
- `apply_plan(plan, approved=False)`
- `get_schema_history(table_name, limit=10)`

### MaintenanceReadModel

Maintenance and observability read-model contract:

- `load_maintenance_status()`
- `render_maintenance_prometheus()`

### AlertSink

Alert delivery contract:

- `send_alert(...)`

### AccessPolicy

Value object used by governance providers:

- `policy_id`
- `principal`
- `table_pattern`
- `action`
- `effect`
- `columns`
- `row_filter`
- `data_masking`

---

## Entry Point Registration

Plugins are discovered at runtime via Python [entry points](https://packaging.python.org/en/latest/specifications/entry-points/). Declare them in your package's `pyproject.toml`:

| Plugin type | Entry point group |
|-------------|-------------------|
| Source connectors | `phlo.plugins.sources` |
| Quality checks | `phlo.plugins.quality` |
| Transformations | `phlo.plugins.transforms` |
| Services | `phlo.plugins.services` |
| CLI commands | `phlo.plugins.cli` |
| Hooks | `phlo.plugins.hooks` |
| Catalogs | `phlo.plugins.catalogs` |
| Asset providers | `phlo.plugins.assets` |
| Resource providers | `phlo.plugins.resources` |
| Orchestrators | `phlo.plugins.orchestrators` |

### pyproject.toml example

```toml
[project.entry-points."phlo.plugins.sources"]
my_source = "my_package.source:MySourceConnector"

[project.entry-points."phlo.plugins.services"]
redis = "my_package.services:RedisService"

[project.entry-points."phlo.plugins.cli"]
hello = "my_package.cli:MyCliPlugin"
```

The entry point **name** becomes the plugin identifier. The **value** points to the plugin class (which is instantiated automatically during discovery).

### Discovery behavior

- Auto-discovery defaults to `plugins_auto_discover=true`.
- `PHLO_NO_AUTO_DISCOVER` has disable precedence (truthy disables, falsy does not disable).
- Plugins can be filtered via `plugins_whitelist` / `plugins_blacklist` in settings.
- Discovery validates that each plugin is an instance of the expected base class for its entry point group.

See also: [Configuration Reference](configuration-reference.md) for plugin settings.
