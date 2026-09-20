# Extending Phlo

Phlo extensions let you add a capability without changing the core workflow model. This post explains the plugin boundaries, shows the smallest useful service plugin shape, and connects hooks and Observatory extensions to the same discovery system.

## The problem from first principles

Every platform eventually needs a local integration. A team may need a new source connector, a service container, a quality provider, a CLI command, or a panel that shows an operational signal. Copying the whole platform for each integration creates a fork. Making the core know every provider creates a different kind of coupling.

A plugin boundary keeps the changing responsibility with the package that owns it. A service plugin describes a managed service. An asset provider creates assets and checks. A resource provider supplies a catalogue, object store, or query engine. A CLI plugin adds commands. A hook plugin observes lifecycle events without owning execution.

The best extension is narrow enough to remove without rewriting the project. It should expose its capability through a documented entry point, report a useful identity, and fail with a message that tells the operator what is missing. It should not silently change how unrelated assets run.

Capabilities describe what a workflow needs, while plugins describe how an installed package supplies it. Discovery uses Python entry points. The registry can validate plugin metadata before a run starts, and a project can keep optional capabilities absent until a declaration needs them.

This model also gives users a way to reason about support. A package can provide a capability without being part of the default template. A service can be installed but disabled by default. A hook can observe a run without becoming a required dependency. The capability registry makes those distinctions visible to the platform and to the person configuring a project.

Before writing a plugin, check whether an existing package already supplies the capability. If you still need an extension, start with one user action and one verification command. Keep its configuration near the project declaration, document the entry point, and make removal as clear as installation. That discipline keeps the extension aligned with the asset model instead of creating a parallel platform.

You can now use Phlo as a learner and as an extender. You can create the tutorial project, ingest a partition, query its table, add a SQL model, attach checks, evolve a contract, inspect signals, recover a failed run, measure a change, and decide where an extension belongs. Each step remains visible through the project files and Dagster's run record.

## How Phlo approaches it

Phlo discovers plugin families through entry-point groups such as `phlo.plugins.services`, `phlo.plugins.assets`, `phlo.plugins.quality`, `phlo.plugins.cli`, and `phlo.plugins.hooks`. The plugin API exports base classes for services, source connectors, quality providers, ingestion providers, transformations, catalogues, governance, assets, resources, orchestrators, CLI commands, and hooks.

The smallest useful plugin should have one responsibility and a visible verification path. A service plugin supplies metadata and a service definition with an image, port, health check, and category. After installation, `phlo plugin list`, `phlo plugin info`, and `phlo plugin check` make discovery visible.

Hooks are for observation and reaction. A hook registration can filter event types, asset keys, or tags and choose a failure policy. Alerting can consume hook events. Lineage, telemetry, and evidence integrations can also translate events without taking over asset execution.

Observatory extensions add navigation, routes, slots, and settings through an extension manifest. Keep the manifest compatible with the Observatory version and ship the UI assets with the package. The extension should remain optional when the underlying signal is optional.

## Try it

Start from the complete service plugin example in [Write a plugin](../guides/write-a-plugin.md). Its core class is:

```python
from __future__ import annotations

from phlo.plugins.base import PluginMetadata, ServicePlugin


class MyDbServicePlugin(ServicePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="mydb",
            version="0.1.0",
            description="MyDB service for local development",
            author="Data Team",
            tags=["database"],
        )

    @property
    def service_definition(self) -> dict:
        return {
            "name": "mydb",
            "description": "MyDB service for local development",
            "category": "core",
            "default": False,
            "compose": {
                "image": "mydb/mydb:1.0",
                "ports": ["9000:9000"],
                "healthcheck": {
                    "test": ["CMD", "mydb", "health"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 5,
                },
            },
        }
```

Register it with the verified service entry-point group:

```toml
[project.entry-points."phlo.plugins.services"]
mydb = "my_phlo_service.plugin:MyDbServicePlugin"
```

Install the package and inspect the discovered plugins:

```bash
uv pip install -e .
phlo plugin list
phlo plugin info mydb
phlo plugin check
phlo services list
```

The plugin guide also shows how to scaffold a project-local plugin. Use [Hooks and events](../reference/hooks-and-events.md) when the extension observes events instead of creating a service.

## Mental model to keep

- A capability names a need.
- A plugin supplies one implementation of that need.
- Entry points make installed plugins discoverable.
- Service definitions describe infrastructure, not workflow logic.
- Hooks observe events without becoming the run path.
- Observatory extensions should declare compatibility and remain optional when appropriate.

## Where this goes wrong

- **The entry-point group is wrong.** The package installs but discovery does not find the plugin.
- **The plugin owns too many responsibilities.** Installation, testing, and failure diagnosis become harder when one package combines unrelated boundaries.
- **A service lacks a health check.** Compose can start a process without proving that the service is ready.
- **An Observatory manifest omits compatibility.** The UI can load an extension that does not match the running Observatory.
- **A hook raises on an optional signal.** Choose a failure policy that matches whether the observed event is essential to the run.

## Next

You can now create a project, ingest and transform data, check its quality, evolve its contract, observe its runs, respond to incidents, optimise measured work, and extend the platform. Read [Plugins and capabilities](../concepts/plugins-and-capabilities.md), [Plugin API](../reference/plugin-api.md), and [Hooks and events](../reference/hooks-and-events.md) when you are ready to design your own boundary.
