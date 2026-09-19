# Write a plugin

This guide builds a complete service plugin, registers it with the verified entry-point group, and confirms that Phlo discovers its Compose definition.

## Before you start

- You have a package workspace with `src/my_phlo_service/` and a Python environment containing `phlo`.
- You have a Docker service image and a `service.yaml` definition that can run without project secrets.
- You want the service to be discoverable by `phlo services list`.

## 1. Write the service plugin

Create `src/my_phlo_service/plugin.py`. `ServicePlugin` requires a `metadata` property and a `service_definition` property. The latter has the same shape as a package `service.yaml`.

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

The plugin now exposes a typed identity and a Compose fragment. The visible result is a service definition that the registry can validate before Docker starts.

## 2. Register the entry point

Add this exact group to `pyproject.toml`:

```toml
[project.entry-points."phlo.plugins.services"]
mydb = "my_phlo_service.plugin:MyDbServicePlugin"
```

Installing the package makes the entry point visible to `importlib.metadata`. Phlo discovers the class on the next process start.

## 3. Provide a package-local service file

For a larger definition, subclass `PackageYamlServicePlugin` and place `service.yaml` beside the package module:

```python
from phlo.plugins.base import PackageYamlServicePlugin


class MyDbYamlServicePlugin(PackageYamlServicePlugin):
    pass
```

The base class loads `service.yaml` from the subclass's top-level package, so the rendered service remains versioned with the plugin package.

## 4. Install and inspect discovery

```bash
uv pip install -e .
phlo plugin list --type services
phlo plugin info mydb
phlo plugin check
phlo services list
```

`plugin list` shows the discovered entry point, `plugin info` shows metadata, `plugin check` runs plugin diagnostics, and `services list` includes the service when its plugin loaded successfully.

## 5. Use a project-local scaffold

The CLI can create a local plugin package beneath `plugins/`:

```bash
phlo plugin create mydb --type service --path ./plugins/
```

The generated package is project-local source material. Install it with `uv pip install -e plugins/mydb` so entry-point discovery sees it in the active environment.

## Verify

```bash
phlo plugin list --type services --json
```

The JSON envelope contains an installed `mydb` entry with its package and plugin metadata. If it is absent, check the editable install and the entry-point group before changing service code.

## Related

- [Choose your stack](choose-your-stack.md) for provider capabilities and service selection.
- [Plugin API](../reference/plugin-api.md) for plugin base classes and lifecycle interfaces.
- [Project layout](../reference/project-layout.md) for project-local directories.
