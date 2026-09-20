# Capabilities and plugins

Phlo discovers Python plugins through package entry points. A canonical entry point must load either an instance of the family class or a class that Phlo can instantiate without arguments. Every plugin supplies `PluginMetadata`; provider families return typed specifications that Phlo adds to the capability registry.

The authoritative family map is [`PLUGIN_FAMILIES`](../../src/phlo/plugins/discovery/_plugin_constants.py). The specification types are defined in [`phlo.capabilities.specs`](../../src/phlo/capabilities/specs.py).

## Entry-point families

| Family | Entry-point group | Accepted object | Contribution |
| --- | --- | --- | --- |
| Source connector | `phlo.plugins.sources` | `SourceConnectorPlugin` | Source connector implementation |
| Quality check | `phlo.plugins.quality` | `QualityCheckPlugin` | Quality checks |
| Quality provider | `phlo.plugins.quality_providers` | `QualityProviderPlugin` | Quality decorator/provider API |
| Ingestion provider | `phlo.plugins.ingestion_providers` | `IngestionProviderPlugin` | Ingestion decorator/provider API |
| Transformation provider | `phlo.plugins.transformation_providers` | `TransformationProviderPlugin` | Transformation decorator/provider API |
| Transformation | `phlo.plugins.transforms` | `TransformationPlugin` | Transformation implementation |
| Service | `phlo.plugins.services` | `ServicePlugin` | Compose service definition |
| CLI command | `phlo.plugins.cli` | `CliCommandPlugin` | Click command or group |
| Hook | `phlo.plugins.hooks` | `HookPlugin` | `HookRegistration` values |
| Catalog | `phlo.plugins.catalogs` | `CatalogPlugin` | Catalog implementation |
| Asset provider | `phlo.plugins.assets` | `AssetProviderPlugin` | `AssetSpec` and optional `AssetCheckSpec` values |
| Resource provider | `phlo.plugins.resources` | `ResourceProviderPlugin` | `ResourceSpec` values and typed capability specifications |
| Orchestrator | `phlo.plugins.orchestrators` | `OrchestratorAdapterPlugin` | Orchestrator adapter |

Three loaders sit outside that canonical table:

| Entry-point group | Accepted object | Purpose |
| --- | --- | --- |
| `phlo.plugins.observatory` | `ObservatoryExtensionPlugin` | Observatory UI manifest and packaged assets; see [Observatory extensions](observatory-extensions.md) |
| `phlo.project_templates` | A callable template factory | Project templates loaded by the [template registry](../../src/phlo/cli/templates/registry.py) |
| `phlo.dagster.frameworks` | A framework adapter accepted by the [Dagster framework loader](../../packages/phlo-dagster/src/phlo_dagster/framework/discovery.py) | Dagster-specific framework discovery |

## Loading, identity, and failure semantics

The canonical loader applies these rules:

1. `plugins_enabled=false` returns empty family lists. `PHLO_NO_AUTO_DISCOVER` controls automatic discovery separately.
2. The blacklist wins. If the whitelist is non-empty, only names on it load.
3. Phlo loads the entry point, instantiates classes, checks `Plugin`, then checks the family subclass.
4. With automatic registration, Phlo registers and initialises each plugin immediately. The registry key is the family prefix plus `metadata.name`. Registration uses replacement semantics, so a later plugin with the same key replaces the earlier plugin after the old instance is cleaned up. A failed replacement restores the previous registration.
5. Non-strict discovery isolates import, construction, type, registration, and initialisation failures. It logs them and continues. A supplied failure sink receives load exceptions. Strict discovery raises `PluginDiscoveryError` at the first failure. Blacklisted and non-whitelisted entries are skipped, not failures.

These behaviours are implemented in the [loader](../../src/phlo/plugins/discovery/_plugin_loading.py), [lifecycle coordinator](../../src/phlo/plugins/discovery/_plugin_lifecycle.py), and [registry](../../src/phlo/plugins/discovery/registry.py), and are covered by the [loading tests](../../tests/plugins/test_plugin_loading.py).

## Capability specifications and resolution

`ResourceProviderPlugin` can return these capability specification families: table stores, catalogues, catalogue scanners, query engines, maintenance executors and read models, object stores, quality backends, metadata catalogues, lineage sinks, governance and authorisation backends, authentication providers, publish targets, alert sinks, API backends, secret backends, schema migrators, data-migration sources, and observability backends. The complete method list is the contract in [`providers.py`](../../src/phlo/plugins/base/providers.py). Other specification families, including backup contributors, settings stores, workflow authoring, evidence and readiness contributions, also use the central [`CapabilityRegistry`](../../src/phlo/capabilities/registry.py).

Capability keys are `(capability_type, spec.name)`. Registration replaces an existing key. Listing is deterministic. A named lookup must match exactly. An unnamed lookup resolves in this order:

1. the runtime context override;
2. `PHLO_DEFAULT_CAPABILITIES` configuration;
3. the project's capability defaults;
4. the family default, currently `object_store=minio`, when installed;
5. the sole installed provider.

An unknown name, no providers, or multiple providers without a configured/default choice returns `None`. Callers decide whether that is optional. Required declarations in `PluginMetadata.requires_capabilities` accept `type` or `type:name`; the requirement checker reports every unresolved declaration. See the [resolver](../../src/phlo/capabilities/resolver.py) and [runtime tests](../../tests/plugins/test_capabilities_runtime.py).

Each specification carries a `CapabilitySupport` declaration. Treat that declaration as provider evidence, not as an inference from installation. The support fields and matching rules are in [`support.py`](../../src/phlo/capabilities/support.py); package and service support tiers are frozen separately in [`registry/support/v1.json`](../../registry/support/v1.json).

## Minimal package examples

All examples need a `PluginMetadata` property; only the family-specific parts are shown.

### Service

```python
from phlo.plugins import service_plugin_class

WidgetService = service_plugin_class(
    "WidgetService",
    name="widget",
    version="1.0.0",
    service_definition_file="service.yaml",
)
```

```toml
[project.entry-points."phlo.plugins.services"]
widget = "acme_widget.plugin:WidgetService"
```

Package `service.yaml` beside the module and include it in the wheel. The helper reads that manifest into the `ServicePlugin.service_definition` contract.

### Asset provider

```python
from phlo.capabilities import AssetSpec
from phlo.plugins import AssetProviderPlugin, PluginMetadata

class WidgetAssets(AssetProviderPlugin):
    metadata = PluginMetadata(name="widget", version="1.0.0")

    def get_assets(self):
        return [AssetSpec(
            key="widget/orders",
            group="widget",
            description="Orders imported by Widget",
        )]
```

```toml
[project.entry-points."phlo.plugins.assets"]
widget = "acme_widget.plugin:WidgetAssets"
```

### Resource and capability provider

```python
from phlo.capabilities import ObjectStoreSpec, ResourceSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin

class WidgetResources(ResourceProviderPlugin):
    metadata = PluginMetadata(name="widget", version="1.0.0")

    def get_resources(self):
        return [ResourceSpec(name="widget_client", resource=WidgetClient())]

    def get_object_stores(self):
        return [ObjectStoreSpec(name="widget", provider=WidgetObjectStore())]
```

```toml
[project.entry-points."phlo.plugins.resources"]
widget = "acme_widget.plugin:WidgetResources"
```

Discovery calls the provider methods and registers each returned specification. Return an iterable, not the provider object directly.

### Hook

```python
from phlo.plugins import HookPlugin, PluginMetadata
from phlo.plugins.hooks import FailurePolicy, HookRegistration

def report(event):
    print(event.event_type)

class WidgetHooks(HookPlugin):
    metadata = PluginMetadata(name="widget", version="1.0.0")

    def get_hooks(self):
        return [HookRegistration(
            hook_name="widget.report",
            handler=report,
            priority=100,
            failure_policy=FailurePolicy.LOG,
        )]
```

```toml
[project.entry-points."phlo.plugins.hooks"]
widget = "acme_widget.plugin:WidgetHooks"
```

Handlers can be synchronous, asynchronous, or objects implementing the matching handler protocol. Lower priority values run first. A registration can filter by event type, asset key, and tags. `IGNORE`, `LOG`, and `RAISE` determine dispatch-time failure handling; they do not change discovery failures.
