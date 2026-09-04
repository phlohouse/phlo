# ADR 0041: Capability Primitives and Orchestrator Adapters

## Status

### Accepted

## Context

Phlo has a unified plugin registry and multiple plugin types (services, sources, quality, transforms,
hooks, Dagster extensions, CLI). Today, capability packages (ingestion, quality, dbt) build Dagster
assets directly and import Dagster in core-facing modules. This creates tight coupling:

- Core `phlo` imports Dagster in `src/phlo/plugins/base.py`.
- Capability packages create Dagster assets directly (`phlo-dlt`, `phlo-pandera`, `phlo-dbt`).
- `phlo_dagster.framework.definitions` assumes Dagster is the orchestrator.

We want fully plug-and-play packages that can compose across different orchestrators (Dagster,
Airflow, Prefect, etc.) without hard dependencies or direct imports.

## Decision

Introduce **capability primitives** in core (orchestrator-agnostic specs and protocols) and
**orchestrator adapters** (pluggable translators that map capability specs to orchestrator
definitions).

Key elements:

1. **Capability primitives**: `AssetSpec`, `AssetCheckSpec`, `ResourceSpec`, `PartitionSpec`,
   `ScheduleSpec`, `RunSpec`, and a shared `RuntimeContext` protocol.
2. **Capability registry**: discoverable providers for assets/resources/semantic models without
   depending on an orchestrator.
3. **Orchestrator adapter plugin**: an entry-point group that translates capability specs into
   orchestrator-specific definitions.
4. **No Dagster imports in core**: Dagster-specific APIs move to `phlo-dagster` adapter package.
5. **Capability packages publish specs**: `phlo-dlt`, `phlo-dbt`, `phlo-pandera` emit specs and
   use hook events instead of constructing Dagster assets directly.

## Proposed APIs (Shapes)

### Capability Specs (Core)

```python
@dataclass(frozen=True)
class AssetSpec:
    key: str
    group: str | None
    description: str | None
    kinds: set[str]
    tags: dict[str, str]
    partitions: PartitionSpec | None
    checks: list[AssetCheckSpec]
    run: RunSpec

@dataclass(frozen=True)
class RunSpec:
    fn: Callable[[RuntimeContext], Iterable[RunResult]]
    max_runtime_seconds: int | None
    retries: int | None
    retry_delay_seconds: int | None

class RuntimeContext(Protocol):
    run_id: str | None
    partition_key: str | None
    logger: Logger
    tags: dict[str, str]
    resources: dict[str, Any]
```

### Orchestrator Adapter (New Plugin Type)

```python
class OrchestratorAdapterPlugin(Plugin, ABC):
    @property
    def name(self) -> str: ...

    def build_definitions(
        self,
        assets: list[AssetSpec],
        resources: dict[str, ResourceSpec],
    ) -> Any: ...
```

### Capability Providers (New Entry Points)

```text
phlo.plugins.assets
phlo.plugins.resources
phlo.plugins.semantic
phlo.plugins.orchestrators
```

## Implementation Plan

### Phase 1: Core Capability Layer

- Add capability spec types in `src/phlo/capabilities/`.
- Add `RuntimeContext` protocol and minimal helpers.
- Add registry + discovery for capability providers.
- Remove Dagster imports from `src/phlo/plugins/base.py`.

### Phase 2: Orchestrator Adapter

- Create `OrchestratorAdapterPlugin` base type.
- Implement Dagster adapter in `phlo-dagster`:
  - Translate `AssetSpec` -> Dagster assets/checks/resources.
  - Provide `build_definitions()` entry point.

### Phase 3: Package Refactors

- `phlo-dlt`: publish `AssetSpec` instead of Dagster assets.
- `phlo-dbt`: publish `AssetSpec` and use `DbtTransformer` for execution.
- `phlo-pandera`: publish `AssetCheckSpec` using capability contract.
- Continue emitting hook events for lineage/quality/telemetry.

### Phase 4: Framework Integration

- Replace `phlo_dagster.framework.definitions` Dagster-only path with:
  - discover capability specs
  - load active orchestrator adapter
  - build orchestrator definitions

### Phase 5: Registry + CLI

- Extend registry schema to include:
  - `hooks`, `orchestrator`, `assets`, `resources`, `semantic`, `observability`, `api`, `logging`, `bi`
- Update `phlo plugin list/search` to recognize new types.

## Consequences

### Positive

- Capability packages are truly plug-and-play and orchestrator-agnostic.
- Core no longer depends on Dagster.
- Orchestrator integrations can be swapped cleanly.
- Better DX for package authors (clear, testable specs).

### Negative

- Additional surface area to maintain (capability specs + adapters).
- Migration work for existing packages.
- Requires a clear “active orchestrator” selection strategy.

## Migration Checklist

- [ ] Add capability spec types + registry in core.
- [ ] Add orchestrator adapter plugin type + discovery.
- [ ] Implement Dagster adapter in `phlo-dagster`.
- [ ] Refactor `phlo-dlt` to emit `AssetSpec`.
- [ ] Refactor `phlo-pandera` to emit `AssetCheckSpec`.
- [ ] Refactor `phlo-dbt` to emit `AssetSpec`.
- [ ] Update `phlo_dagster.framework.definitions` to use the adapter.
- [ ] Update registry schema + bundled registry data.
- [ ] Update `docs/guides/plugin-development.md` for new primitives.
- [ ] Add tests for adapter translation and capability discovery.

## Open Questions

Resolved:
- Active orchestrator is selected via config (`PHLO_ORCHESTRATOR`), defaulting to `dagster`.
- Multiple orchestrators are not supported in a single environment.
- Asset specs should live in packages where possible; workflows focus on user-specific assets.

## Related

- ADR 0030: Unified Plugin System with Registry
- ADR 0033: Hook-Based Capability Plugins
