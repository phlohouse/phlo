# Plugin API reference

Phlo plugins are Python classes discovered through entry points. Base classes are exported from `phlo.plugins.base`.

## Plugin

`Plugin` is the base class. Subclasses implement the `metadata` property.

| Member | Type | Meaning |
| --- | --- | --- |
| `metadata` | `PluginMetadata` property | Plugin identity and metadata. |
| `initialize` | `(config: dict[str, Any]) -> None` | Initialize plugin state. |
| `cleanup` | `() -> None` | Release plugin state. |

`PluginMetadata` contains `name`, `version`, `description`, `author`, `license`, `homepage`, `tags`, `dependencies`, and capability support metadata.

## Provider plugin classes

### ServicePlugin

`ServicePlugin` provides container-backed infrastructure.

| Member | Type | Meaning |
| --- | --- | --- |
| `service_definition` | `dict[str, Any]` property | Service declaration. |
| `category` | `str` property | Service category. |
| `is_default` | `bool` property | Whether the service is default. |
| `profile` | `str \| None` property | Optional profile. |
| `requires_capabilities` | `list[str]` property | Required capabilities. |
| `optional_capabilities` | `list[str]` property | Optional capabilities. |
| `get_compose_fragment` | `() -> dict[str, Any]` | Compose fragment. |
| `get_files` | `() -> list[dict[str, str]]` | Initialization files. |
| `get_dependencies` | `() -> list[str]` | Service dependencies. |

`PackageYamlServicePlugin` is the service subclass that reads a package service declaration.

### SourceConnectorPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `fetch_data` | `(config: dict[str, Any]) -> Iterator[dict[str, Any]]` | Fetch source records. |
| `get_schema` | `(config: dict[str, Any]) -> dict[str, str] \| None` | Return source schema. |
| `test_connection` | `(config: dict[str, Any]) -> bool` | Test source connectivity. |

### QualityCheckPlugin

`QualityCheckPlugin[TQualityCheck]` creates provider-specific checks.

| Member | Type | Meaning |
| --- | --- | --- |
| `create_check` | `(*args: Any, **kwargs: Any) -> TQualityCheck` | Build a quality check. |

### QualityProviderPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `get_decorator` | `() -> Callable` | Return the provider decorator. |
| `get_check_classes` | `() -> dict[str, type]` | Return named check classes. |
| `get_schema_extractor` | `() -> Any \| None` | Return schema extractor. |
| `get_schema_base_import` | `() -> tuple[str, str] \| None` | Return schema import. |
| `render_schema_field` | `(...) -> str` | Render a schema field. |
| `render_schema_module` | `(...) -> str` | Render a schema module. |
| `get_reconciliation_checks` | `() -> dict[str, type] \| None` | Return reconciliation checks. |
| `build_checks_from_rules` | `(rules: list[Any]) -> list[Any] \| None` | Convert neutral rules. |

### IngestionProviderPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `get_decorator` | `() -> Callable` | Return the ingestion decorator. |
| `get_asset_retriever` | `() -> Callable[[], list[Any]]` | Return the asset registry accessor. |

### TransformationPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `transform` | `(df: Any, config: dict[str, Any]) -> Any` | Transform data. |
| `get_output_schema` | `(...) -> Any` | Return output schema. |
| `validate_config` | `(config: dict[str, Any]) -> bool` | Validate transform configuration. |

### TransformationProviderPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `get_asset_retriever` | `() -> Callable[[], list[Any]]` | Return transformation assets. |
| `get_cli_plugin` | `() -> Any \| None` | Return an optional CLI plugin. |
| `get_compiler` | `() -> Any \| None` | Return an optional compiler. |
| `get_manifest_loader` | `() -> Any \| None` | Return an optional manifest loader. |

### CatalogPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `targets` | `list[str]` property | Engine targets. |
| `catalog_name` | `str` property | Catalog identifier. |
| `get_properties` | `() -> dict[str, Any]` | Catalog properties. |
| `supports_target` | `(target: str) -> bool` | Check engine support. |

### GovernancePlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `list_policies` | `(*, table_name: str \| None) -> list[dict[str, Any]]` | List policies. |
| `apply_policy` | `(*, policy: AccessPolicy) -> None` | Apply a policy. |
| `revoke_policy` | `(*, policy_id: str) -> None` | Revoke a policy. |
| `check_access` | `(*, principal: str, table_name: str, action: str) -> bool` | Check access. |
| `get_masking_rules` | `(*, table_name: str) -> list[dict[str, Any]]` | Return masking rules. |
| `get_row_filters` | `(*, table_name: str) -> list[dict[str, Any]]` | Return row filters. |
| `get_data_classifications` | `() -> Iterable[dict[str, Any]]` | Return classifications. |

### AssetProviderPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `requires_capabilities` | `list[str]` property | Required capabilities. |
| `optional_capabilities` | `list[str]` property | Optional capabilities. |
| `get_assets` | `() -> Iterable[AssetSpec]` | Return asset specs. |
| `get_checks` | `() -> Iterable[AssetCheckSpec]` | Return check specs. |

### ResourceProviderPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `requires_capabilities` | `list[str]` property | Required capabilities. |
| `optional_capabilities` | `list[str]` property | Optional capabilities. |
| `get_resources` | `() -> Iterable[ResourceSpec]` | Return resource specs. |
| `get_table_stores` | `() -> Iterable[TableStoreSpec]` | Return table stores. |
| `get_catalogs` | `() -> Iterable[CatalogSpec]` | Return catalogs. |
| `get_query_engines` | `() -> Iterable[QueryEngineSpec]` | Return query engines. |

The class also defines provider accessors for maintenance executors, object stores, quality backends, metadata catalogs, lineage sinks, governance backends, authentication providers, publish targets, alert sinks, API backends, secret backends, schema migrators, data migration sources, and observability backends. Their names are `get_maintenance_executors`, `get_object_stores`, `get_quality_backends`, `get_maintenance_read_models`, `get_metadata_catalogs`, `get_lineage_sinks`, `get_governance_backends`, `get_authorization_policy_backends`, `get_authentication_providers`, `get_publish_targets`, `get_alert_sinks`, `get_api_backends`, `get_secret_backends`, `get_schema_migrators`, `get_data_migration_sources`, and `get_observability_backends`.

### OrchestratorAdapterPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `exec_service_name` | `() -> str \| None` | Optional execution service. |
| `build_definitions` | `(...) -> Any` | Build orchestrator definitions. |

### CliCommandPlugin

| Member | Type | Meaning |
| --- | --- | --- |
| `get_cli_commands` | `() -> list[click.Command]` | Return CLI commands. |

## Runtime capability interfaces

Runtime capability interfaces live in `phlo.capabilities.interfaces`, not in the plugin base package. Plugins provide implementations through provider specifications and entry points.

## Hook interfaces

`HookProvider` defines `get_hooks() -> Iterable[HookRegistration]`. `HookHandler` defines `handle_event(event: HookEvent) -> None`. `AsyncHookHandler` defines `handle_event_async(event: HookEvent) -> None`. `HookPlugin` combines `Plugin` and `HookProvider`.

`HookFilter` and `HookRegistration` are dataclasses. `FailurePolicy` is the string enum used by hook registrations.

## Entry point registration

Declare plugin classes in `pyproject.toml` entry-point groups:

| Plugin type | Group |
| --- | --- |
| Source | `phlo.plugins.sources` |
| Quality | `phlo.plugins.quality` |
| Transform | `phlo.plugins.transforms` |
| Service | `phlo.plugins.services` |
| CLI | `phlo.plugins.cli` |
| Hook | `phlo.plugins.hooks` |
| Catalog | `phlo.plugins.catalogs` |
| Asset | `phlo.plugins.assets` |
| Resource | `phlo.plugins.resources` |
| Orchestrator | `phlo.plugins.orchestrators` |

Discovery validates each entry point against the expected plugin base class.
