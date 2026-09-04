# ADR 0033: Hook-Based Capability Plugins

## Status

**Proposed**

## Context

Phlo already supports a unified plugin system with entry-point discovery, a registry,
and base classes for sources, quality checks, transformations, services, Dagster
extensions, and CLI commands. Service lifecycle hooks exist in `service.yaml`, but
there is no first-class, typed hook system for data-lake capabilities such as
semantic layers, observability, telemetry, lineage, publishing, or marts.

This leaves open questions:

- Plugins cannot reliably coordinate or observe pipeline events without importing
  each other directly.
- Capability packages (e.g., metadata, telemetry, semantic models) lack a clean,
  stable contract for interop.
- Service hooks are separate from data workflow events, which fragments the
  extension model.

We need a best-practice, scalable hook system that preserves the current plugin
architecture while enabling new capability plugins to compose cleanly.

## Decision

Introduce a **Hook Bus** with typed events and optional hook protocols to enable
capability plugins (semantic layer, observability, telemetry, lineage, publishing,
and similar) to integrate without direct dependencies.

Key decisions:

1. **Hook Bus in core**: add a lightweight dispatcher responsible for emitting and
   routing events to registered hook providers.
2. **Typed event contexts**: define a small, stable set of event models (dataclasses
   or Pydantic) covering pipeline, quality, publishing, and service lifecycle.
3. **Synchronous by default**: hook dispatch is synchronous in-process for ordering
   and determinism. Hooks should be fast; heavy work must delegate to background
   systems. Async/queued dispatch is a future extension for high-volume telemetry.
4. **Ordering + priority**: hooks register with an integer priority (lower runs first).
   Stable ordering is maintained by `(priority, plugin_name, hook_name)`.
5. **Failure policy**: hooks declare `failure_policy` (ignore/log/raise). Default is
   `log` and continues the pipeline; `raise` can abort the originating flow.
6. **Event filtering**: hook registration includes optional filters (event type,
   asset keys, tags) so hooks only receive relevant events.
7. **Optional hook protocols**: plugins may implement hook protocols without
   changing their primary plugin type.
8. **Hook-only plugins**: add a dedicated entry-point group for plugins whose
   only responsibility is handling hooks.
9. **Unified lifecycle**: map existing `service.yaml` hooks onto Hook Bus events so
   service lifecycle hooks are part of the same system.
10. **Schema evolution**: events carry a `version` field and follow additive-only
   changes; deprecations are documented and removed only on major versions.

## Implementation

### Phase 1: Core Hook System

- Add `src/phlo/hooks/` with:
  - `events.py` defining typed event contexts:
    - `ServiceLifecycleEvent` (pre/post start/stop)
    - `IngestionEvent` (start/end, asset key, table name, run metadata)
    - `TransformEvent` (start/end, tool name, model/asset metadata)
    - `PublishEvent` (start/end, marts/publish metadata)
    - `QualityResultEvent` (check results, severity, asset metadata)
    - `LineageEvent` (edges, asset metadata)
    - `TelemetryEvent` (metric/log/span payloads with tags)
  - `bus.py` implementing `HookBus.emit(event)` with ordering, filtering, and
    failure policy handling.
- Add `src/phlo/plugins/hooks.py` with:
  - `HookRegistration` dataclass (hook_name, handler, priority, filters,
    failure_policy).
  - `HookProvider` Protocol defining `get_hooks()` (declarative registration).
  - `HookHandler` Protocol defining `handle_event()` (single-dispatch convenience).
  - `HookPlugin` ABC for hook-only plugins (inherits `Plugin`).
- Extend `src/phlo/discovery/plugins.py` with a new entry-point group:
  - `phlo.plugins.hooks`
- Extend `src/phlo/discovery/registry.py` to register hook plugins and list them.

### Phase 2: Emit Events from Core Pipelines

- In `phlo-dlt` ingestion decorator, emit `IngestionEvent` start/end.
- In `phlo-pandera`, emit `QualityResultEvent` after check execution.
- In `phlo-dbt` publishing flow, emit `PublishEvent` for marts publishing.
- In `phlo-dbt` or transform orchestration, emit `TransformEvent`.
- In service management (`src/phlo/cli/services.py`), translate `service.yaml`
  hooks into `ServiceLifecycleEvent` and also emit events from start/stop flows.

### Phase 3: Update Capability Packages

- `phlo-openmetadata` subscribes to:
  - `LineageEvent` for lineage publishing
  - `QualityResultEvent` for test results
  - `PublishEvent` to track published marts
- Core telemetry hooks and `phlo-alerting` subscribe to:
  - `TelemetryEvent` for metrics/logs
  - `QualityResultEvent` for alerting
- Add a semantic layer capability contract:
  - Protocol like `SemanticLayerProvider` with `list_models()` and `get_model()`
  - Hook subscription to `PublishEvent` for model refresh

### Phase 4: Registry + DX

- Update `registry/plugins.json` and `src/phlo/plugins/registry_data.json` to
  include hook plugin types.
- Extend `phlo plugin create` scaffolds to include hook providers and config
  examples.
- Update `docs/guides/plugin-development.md` to document hook events and
  capability protocols.
- Add `phlo-testing` helpers (mock bus, event fixtures) so plugin authors can
  test hook handlers in isolation.

## Consequences

### Positive

- **Composable capabilities**: telemetry, metadata, and semantic layers can
  integrate without direct imports.
- **Stable contracts**: protocols + typed events reduce brittle coupling.
- **Unified model**: service hooks and data workflow hooks share the same bus.
- **Scalability**: new capabilities can be added without touching core pipelines.

### Negative

- **More surface area**: additional plugin type and hook definitions to maintain.
- **Event discipline required**: event schemas must remain stable to avoid churn.
- **Blocking risk**: synchronous hooks can slow hot paths if misused.

## Verification

```bash
# Hook discovery and registry
phlo plugin list --type hooks

# Hook dispatch during ingestion
phlo test tests/test_ingestion.py

# Hook dispatch during quality checks
phlo test tests/test_quality_decorator.py

# Service lifecycle hooks map to Hook Bus
phlo services start
phlo services stop
```

## Related

- Prior: [ADR 0030 - Unified Plugin System](./0030-unified-plugin-system-with-registry.md)
- Prior: [ADR 0031 - Observatory as Core with Plugin DX Improvements](./0031-observatory-as-core-and-dx-improvements.md)
