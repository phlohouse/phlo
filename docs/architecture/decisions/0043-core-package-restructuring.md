# ADR 0043: Core Package Restructuring

## Status

**Proposed**

## Context

The `src/phlo` core package has grown organically to 11,490 lines across 54 files. While functional, the structure exhibits several anti-patterns that hinder maintainability:

### Critical Issues

1. **Monolithic CLI files**
   - `cli/services.py`: 1,807 lines with 30 Click commands
   - `cli/plugin.py`: 1,061 lines with 17 Click commands
   - Violates Single Responsibility Principle
   - Difficult to test individual commands

2. **Private subdirectory in public package**
   - `cli/_services/` uses underscore prefix suggesting private/internal
   - Contains essential functionality: containers.py, command.py, compose.py
   - Confusing for users and violates Python conventions

3. **Unclear module boundaries**
   ```text
   discovery/     # Plugin & service discovery
   services/      # Service discovery & composer (overlaps with discovery/)
   plugins/       # Base classes & registry client (related to discovery/)
   ```
   Three top-level modules with overlapping concerns create confusion about where code belongs.

4. **Single-file modules**
   - `publishing/` contains only `trino_to_postgres.py` (315 lines)
   - `utils.py` contains only one 3-line function
   - Either indicates premature abstraction or missing functionality

5. **Large monolithic config**
   - `config.py`: 462 lines with 50+ fields spanning all domains
   - Single Settings class covers database, storage, catalog, orchestration, alerting, etc.
   - Difficult to understand scope and dependencies

### Architectural Concerns

- No clear separation between domain logic, application layer, and infrastructure
- Module organization doesn't reflect architectural boundaries
- Entry point unclear for new developers
- Testing strategy hampered by tight coupling

## Decision

Refactor `src/phlo` into a layered architecture with clear module boundaries:

### Phase 1: CLI Command Decomposition

Split monolithic CLI files into focused command modules:

```text
cli/
├── __init__.py
├── main.py              # Entry point, command registration
├── commands/
│   ├── __init__.py
│   ├── services/        # phlo services commands
│   │   ├── __init__.py
│   │   ├── init.py      # phlo services init
│   │   ├── start.py     # phlo services start
│   │   ├── stop.py      # phlo services stop
│   │   ├── logs.py      # phlo services logs
│   │   └── ...          # Additional service commands
│   ├── plugin/          # phlo plugin commands
│   │   ├── __init__.py
│   │   ├── list.py      # phlo plugin list
│   │   ├── info.py      # phlo plugin info
│   │   ├── search.py    # phlo plugin search
│   │   ├── install.py   # phlo plugin install
│   │   └── ...          # Additional plugin commands
│   └── workflow/        # phlo workflow create
│       ├── __init__.py
│       └── create.py
└── infrastructure/      # Renamed from _services
    ├── __init__.py
    ├── containers.py
    ├── command.py
    ├── compose.py
    └── utils.py
```

**Benefits:**
- Each command is 50-200 lines, easily testable
- Clear ownership and responsibility
- Easy to add new commands without merge conflicts
- Follows Click best practices for large CLI apps

### Phase 2: Plugin System Consolidation

Merge overlapping plugin/discovery/services modules:

```text
plugins/
├── __init__.py          # Public API exports
├── base/                # Base classes (split from base.py)
│   ├── __init__.py
│   ├── plugin.py        # Plugin, PluginMetadata
│   ├── source.py        # SourceConnectorPlugin
│   ├── quality.py       # QualityCheckPlugin
│   ├── transform.py     # TransformationPlugin
│   ├── service.py       # ServicePlugin
│   ├── catalog.py       # CatalogPlugin
│   ├── orchestrator.py  # OrchestratorAdapterPlugin
│   └── providers.py     # AssetProviderPlugin, ResourceProviderPlugin
├── discovery/           # Discovery & registration
│   ├── __init__.py
│   ├── plugins.py       # Entry point discovery
│   ├── services.py      # Service discovery
│   └── registry.py      # Plugin registry client
└── compose/             # Compose generation (from services/)
    ├── __init__.py
    └── composer.py
```

**Benefits:**
- Single top-level module for plugin system
- Base classes separated by plugin type for easier navigation
- Clear separation between discovery (finding plugins) and composition (building infrastructure)

### Phase 3: Configuration Domain Split

Split monolithic config into domain-specific classes:

```text
config/
├── __init__.py          # Exports get_settings(), unified Settings
├── base.py              # BaseConfig, common utilities
├── database.py          # DatabaseConfig (postgres, lineage)
├── storage.py           # StorageConfig (minio, s3)
├── catalog.py           # CatalogConfig (nessie, iceberg)
├── query.py             # QueryConfig (trino)
├── orchestration.py     # OrchestrationConfig (dagster)
├── observability.py     # ObservabilityConfig (logging, metrics)
├── alerting.py          # AlertingConfig (slack, email, pagerduty)
├── integration.py       # IntegrationConfig (openmetadata, dbt, superset)
└── settings.py          # Unified Settings (composes all configs)
```

**Benefits:**
- Domain experts can own their config section
- Easier to understand dependencies and relationships
- Better IDE navigation and autocomplete
- Settings class becomes composition of domain configs

### Phase 4: Root-Level Cleanup

```text
Before:
src/phlo/
├── ingestion.py         # What does this do?
├── transformer.py       # Is this used?
├── publishing/
│   └── trino_to_postgres.py
└── utils.py             # One 3-line function

After:
src/phlo/
├── operations/          # New: operational utilities
│   ├── __init__.py
│   ├── ingestion.py     # Moved from root
│   ├── transformer.py   # Moved from root
│   └── publishing.py    # Moved from publishing/trino_to_postgres.py
└── utils/               # Expanded utilities module
    ├── __init__.py
    ├── dict.py          # compact_dict
    ├── path.py          # Path utilities
    └── validation.py    # Common validators
```

**Benefits:**
- Clear purpose for root-level files
- Avoid single-file modules
- Group related operational concerns

### Phase 5: Establish Layered Architecture

Document architectural layers in module structure:

```text
src/phlo/
├── domain/              # Business logic (if applicable)
├── operations/          # Core operations (ingestion, transform, publish)
├── capabilities/        # Capability specs (existing, good structure)
├── plugins/             # Plugin system
├── config/              # Configuration management
├── framework/           # Framework integration (dagster, etc.)
├── orchestrators/       # Orchestrator adapters
├── infrastructure/      # External integrations
├── hooks/               # Event system
└── cli/                 # Presentation layer
```

## Implementation Plan

### Milestone 1: CLI Decomposition (Breaking Changes)
- [ ] Create `cli/commands/` structure
- [ ] Split `cli/services.py` into individual command files
- [ ] Split `cli/plugin.py` into individual command files
- [ ] Rename `cli/_services/` → `cli/infrastructure/`
- [ ] Update imports throughout codebase
- [ ] Update tests
- [ ] Update documentation

**Estimated effort:** 3-4 days
**Risk:** High (touches many imports)

### Milestone 2: Plugin System Consolidation (Breaking Changes)
- [ ] Create `plugins/base/` directory
- [ ] Split `plugins/base.py` into individual files
- [ ] Move `discovery/` into `plugins/discovery/`
- [ ] Move `services/composer.py` into `plugins/compose/`
- [ ] Remove empty `services/` directory
- [ ] Update imports
- [ ] Update tests

**Estimated effort:** 2-3 days
**Risk:** Medium (well-isolated module)

### Milestone 3: Configuration Split (Non-Breaking)
- [ ] Create `config/` directory structure
- [ ] Create domain-specific config classes
- [ ] Create unified Settings class that composes domains
- [ ] Keep `config.py` as compatibility shim
- [ ] Deprecate direct import of Settings from config.py
- [ ] Migrate callers to use `config.get_settings()`
- [ ] Remove compatibility shim in next major version

**Estimated effort:** 2-3 days
**Risk:** Low (can maintain backward compat)

### Milestone 4: Root Cleanup (Non-Breaking)
- [ ] Create `operations/` directory
- [ ] Move root-level operational files
- [ ] Expand or remove `utils.py`
- [ ] Create compatibility shims
- [ ] Update imports gradually

**Estimated effort:** 1-2 days
**Risk:** Low

## Consequences

### Positive

1. **Improved maintainability**
   - Smaller, focused files easier to understand and modify
   - Clear module boundaries reduce cognitive load
   - Testing individual commands becomes trivial

2. **Better developer onboarding**
   - New developers can navigate codebase by layer
   - Module names clearly indicate purpose
   - Architecture documented in structure

3. **Reduced merge conflicts**
   - Commands in separate files reduce conflicts
   - Clearer ownership of modules

4. **Easier testing**
   - Individual commands can be tested in isolation
   - Configuration domains can be validated separately
   - Plugin types can be tested independently

5. **Foundation for growth**
   - Clear patterns for adding new commands
   - Established boundaries for new features
   - Scalable architecture

### Negative

1. **Migration effort**
   - Breaking changes require updating all imports
   - Package users need to update imports
   - Documentation needs comprehensive updates

2. **Temporary complexity**
   - During migration, both old and new structure exist
   - Compatibility shims add temporary complexity

3. **More files to navigate**
   - Deep directory structures can be harder to browse
   - IDE file search becomes more important

4. **Testing burden**
   - Must update all existing tests
   - Integration tests may need restructuring

## Migration Strategy

### For Package Users (Breaking Changes)

Provide migration guide with import mapping:

```python
# Before (0.3.0)
from phlo.config import Settings, get_settings
from phlo.plugins.discovery import ServiceDiscovery

# After (0.4.0)
from phlo_postgres.settings import get_settings as get_postgres_settings
from phlo.plugins.discovery import ServiceDiscovery
```text

### For Internal Code

1. Remove shims and update imports immediately
2. Migrate internal code in one pass
3. Update docs/tests alongside code

### Testing Strategy

1. Maintain existing test coverage during migration
2. Add new tests for individual command modules
3. Use pytest fixtures for config domain testing
4. Integration tests verify compatibility shims

## Verification

```bash
# After Phase 1: CLI works
uv run phlo services start
uv run phlo plugin list
uv run phlo workflow create

# After Phase 2: Plugin discovery works
uv run pytest tests/test_plugin_system.py -v

# After Phase 3: Configuration works
uv run pytest tests/test_config.py -v

# All phases: No regressions
uv run pytest -v
```

## Alternatives Considered

### Alternative 1: Do Nothing

**Rejected.** Technical debt compounds. The current structure is becoming increasingly difficult to maintain as the codebase grows. Addressing now prevents larger refactoring later.

### Alternative 2: Gradual File-by-File Refactoring

**Rejected.** Piecemeal approach lacks coherent vision and can result in inconsistent patterns. Better to establish clear architecture once.

### Alternative 3: Complete Rewrite

**Rejected.** Too risky. Incremental migration maintains functionality while improving structure.

## Related

- ADR 0007: CLI Services Architecture (original CLI refactoring decision)
- ADR 0030: Unified Plugin System (plugin system design)
- ADR 0033: Hook-based Capability Plugins (capability primitives)
- ADR 0041: Capability Primitives and Orchestrator Adapters (layered architecture)

## References

- [Python Application Layouts](https://realpython.com/python-application-layouts/)
- [Click Complex Applications](https://click.palletsprojects.com/en/8.1.x/complex/)
- [Pydantic Settings Management](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
