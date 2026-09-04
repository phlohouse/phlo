# ADR 0031: Observatory as Core with Plugin DX Improvements

## Status

**Accepted** - Implementation in progress

## Context

The current Phlo architecture has Observatory as an optional plugin package (`phlo-observatory`), installed separately via `pip install phlo-observatory`. This creates several issues:

### Problems with Observatory as Plugin

1. **Awkward DX** - Observatory is "the face of Phlo" but users must install it separately
2. **No phlo access** - As a Node.js TanStack Start app, Observatory cannot access phlo internals (installed plugins, configs, service status)
3. **Mismatch with purpose** - Plugins should be swappable alternatives (dagster vs airflow); Observatory has no alternative
4. **Version coupling** - Observatory features depend on phlo-api endpoints that don't exist yet

### Additional DX Issues

1. **No user overrides** - Users cannot customize installed service ports/environment in `phlo.yaml`
2. **No enable/disable** - Cannot easily disable default services
3. **Naming inconsistency** - Some code still references "Cascade" instead of "Phlo"
4. **Dual discovery systems** - Confusing separation between `plugins/discovery.py` and `services/discovery.py`
5. **Deprecated package** - `phlo-fastapi` is superseded by postgrest and the new phlo-api

## Decision

### 1. Make Observatory a Core Service

Observatory ships with `pip install phlo`, not as a separate plugin.

```
# Current
pip install phlo phlo-observatory

# New
pip install phlo  # Includes Observatory
```

### 2. Create phlo-api Backend

A new core Python FastAPI service that exposes phlo internals:

- `GET /api/plugins` - List installed plugins
- `GET /api/services` - List services and status
- `GET /api/config` - Read phlo.yaml configuration
- `POST /api/services/{name}/restart` - Restart a service (future)

Observatory calls phlo-api to display plugin status, configs, etc.

### 3. Add User Service Overrides in phlo.yaml

```yaml
services:
  observatory:
    port: 8080
    environment:
      CUSTOM_VAR: "value"
  superset:
    enabled: false
  custom-api:
    type: inline
    image: my-registry/api:latest
```

### 4. Add `[defaults]` Extra

```toml
[project.optional-dependencies]
defaults = ["phlo-dagster", "phlo-postgres", "phlo-trino", "phlo-nessie", "phlo-minio"]
```

Enables: `pip install phlo[defaults]`

### 5. Consolidate Discovery

Merge plugin and service discovery into single module:

```
src/phlo/discovery/
├── plugins.py   # Entry point discovery
├── services.py  # Service loading (core + plugins)
└── registry.py  # Remote registry
```

### 6. Remove phlo-fastapi

Package is superseded by:

- **postgrest** for auto-generated REST APIs from Postgres
- **phlo-api** for phlo-specific internal APIs

### 7. Naming Cleanup

Replace all "Cascade" references with "Phlo".

## Implementation

See [implementation-roadmap.md](../goals/implementation-roadmap.md) for detailed phases.

### Core vs Packages Model

```
CORE (bundled with pip install phlo):
├── phlo library (CLI, config, framework glue)
└── plugin discovery + service orchestration

PACKAGES (swappable, installed separately):
├── phlo-observatory
├── phlo-api
├── phlo-dagster / phlo-airflow
├── phlo-postgres / phlo-mysql
├── phlo-trino / phlo-duckdb
├── phlo-nessie
├── phlo-minio
├── phlo-superset / phlo-metabase
├── phlo-grafana
└── etc.
```

### File Changes

| Action | Path                                                             |
| ------ | ---------------------------------------------------------------- |
| Create | `packages/phlo-observatory/src/phlo_observatory/service.yaml`    |
| Create | `packages/phlo-api/`                                             |
| Update | `src/phlo/services/discovery.py` - Plugin-only service discovery |
| Update | `src/phlo/services/composer.py` - Apply user overrides           |
| Update | `src/phlo/config_schema.py` - Add `ServiceOverride` model        |
| Update | `pyproject.toml` - Workspace packages for services               |
| Delete | `src/phlo/core_services/`                                        |

## Consequences

### Positive

- **Better DX** - Observatory available immediately after `pip install phlo`
- **Observatory empowered** - Can show plugins, configs, service status via phlo-api
- **User customization** - Override service configs in phlo.yaml
- **Cleaner architecture** - Single discovery flow, consistent naming
- **Focused packages** - Each package represents a swappable component

### Negative

- **Larger core install** - phlo now includes Observatory assets
- **Build complexity** - CI must build Observatory's Node.js app
- **Migration** - Existing users with `phlo-observatory` installed need migration path

### Migration

For users who already have `phlo-observatory` installed:

```bash
pip uninstall phlo-observatory  # Now bundled with core
pip install --upgrade phlo      # Gets new version with Observatory
```

## Verification

```bash
# Observatory starts without separate install
pip install phlo
phlo services start
curl http://localhost:3001  # Observatory responds

# phlo-api accessible
curl http://localhost:4000/api/plugins

# Service overrides work
cat >> phlo.yaml << EOF
services:
  observatory:
    port: 8080
EOF
phlo services start
curl http://localhost:8080  # Observatory on custom port

# Disable service works
cat >> phlo.yaml << EOF
services:
  superset:
    enabled: false
EOF
phlo services start
docker ps | grep superset  # Should not appear
```

## Related

- Goals: [plugin-dx.md](../goals/plugin-dx.md)
- Roadmap: [implementation-roadmap.md](../goals/implementation-roadmap.md)
- Prior: [ADR 0030 - Unified Plugin System](./0030-unified-plugin-system-with-registry.md)
