# ADR 0030: Unified Plugin System with Registry

## Status

**Accepted** - Implemented in epic `phlo-930`

## Context

Phlo's plugin system originally supported three Python-based plugin types (source connectors, quality checks, transformations). Docker-based services (postgres, dagster, minio, nessie, trino) were managed separately via `service.yaml` files in `services/core/`.

This created:

- **Inconsistent extension models**: Python plugins vs YAML-defined services
- **No discoverability**: Users couldn't easily find available plugins
- **Duplicated definitions**: Service configs existed in both `services/core/` and required manual maintenance
- **No centralized registry**: Plugin metadata scattered across packages

## Decision

Implement a **unified plugin system** that:

1. **Adds `ServicePlugin` type** - Docker-based services distributed as Python packages
2. **Creates a plugin registry** - JSON catalog of available plugins (hosted + bundled fallback)
3. **Enhances CLI** - `phlo plugin search/install/update` commands
4. **Packages core services** - phlo-postgres, phlo-dagster, phlo-minio, phlo-nessie, phlo-trino
5. **Bundles core plugins** - phlo-core-plugins with quality checks and REST API source
6. **Integrates Observatory** - Plugin management UI in the Hub

## Implementation

### Phase 1: Plugin Type Extension

- `ServicePlugin` base class in `src/phlo/plugins/base.py`
- `phlo.plugins.services` entry point group
- `PluginRegistry` extended with service methods

### Phase 2: Plugin Registry

- `registry/plugins.json` - Authoritative plugin catalog
- `registry/schema/v1.json` - JSON Schema validation
- `src/phlo/plugins/registry_client.py` - Fetch/cache/search logic
- `src/phlo/plugins/registry_data.json` - Bundled fallback for offline use
- CLI commands: `search`, `install`, `update` with `--json` output

### Phase 3: Core Plugins Package

- `packages/phlo-core-plugins/` containing:
  - Quality checks: null_check, uniqueness_check, freshness_check, schema_check
  - Sources: rest_api
- Added as dependency in main `pyproject.toml`

### Phase 4: Service Plugin Packages

- `packages/phlo-postgres/`
- `packages/phlo-dagster/` (includes dagster-daemon)
- `packages/phlo-minio/` (includes minio-setup)
- `packages/phlo-nessie/`
- `packages/phlo-trino/`

Each package bundles its `service.yaml` and uses `importlib.resources` to load definitions.

### Phase 5: Cleanup

- Removed `services/core/` directory (12 files, 402 lines)
- `ServiceDiscovery` now loads services exclusively from plugins

### Phase 6: Observatory Integration

- `packages/phlo-observatory/src/phlo_observatory/src/server/plugins.server.ts` - Server functions
- `packages/phlo-observatory/src/phlo_observatory/src/routes/hub/plugins.tsx` - Plugin management page

## Consequences

### Positive

- **Unified extension model**: Services and data plugins share the same discovery mechanism
- **Discoverability**: Registry enables `phlo plugin search` for finding plugins
- **Distribution**: Services installable via pip like any other plugin
- **Reduced maintenance**: Single source of truth for service definitions
- **Offline support**: Bundled registry fallback when remote unavailable

### Negative

- **More packages to maintain**: 6 new packages under `packages/`
- **Build complexity**: Workspace management with uv

## Verification

```bash
# Tests pass
uv run pytest tests/test_plugin_system.py tests/test_plugin_registry.py tests/test_cli_plugin.py -v

# CLI works
uv run phlo plugin list --json
uv run phlo plugin search postgres

# Service discovery works without services/core/
uv run pytest tests/test_services_discovery.py -v
```

## Related

- Epic: `phlo-930`
- Beads: `phlo-930.1` through `phlo-930.15`
