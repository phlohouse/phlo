# phlo-dbt

dbt transformation integration for Phlo.

## Description

Integrates dbt (data build tool) with the Phlo lakehouse. Provides asset specs from dbt models, CLI commands, and automatic project discovery. Orchestrator adapters (for example `phlo-dagster`) translate the specs into runtime assets when installed.

## Installation

```bash
pip install phlo-dbt
# or
phlo plugin install dbt
```

## Configuration

| Variable            | Default                               | Description                   |
| ------------------- | ------------------------------------- | ----------------------------- |
| `DBT_PROJECT_DIR`   | `workflows/transforms/dbt`                      | Path to dbt project directory |
| `DBT_PROFILES_DIR`  | `workflows/transforms/dbt/profiles`             | Path to dbt profiles          |
| `DBT_MANIFEST_PATH` | `workflows/transforms/dbt/target/manifest.json` | Path to dbt manifest          |
| `DBT_CATALOG_PATH`  | `workflows/transforms/dbt/target/catalog.json`  | Path to dbt catalog           |
| `DBT_GROUP_EXECUTION` | `true` | Build compatible assets in one dbt invocation |

### Grouped execution

With matching `phlo`, `phlo-dbt`, and `phlo-dagster` versions, compatible models
from one project share a `dbt build` invocation. Existing asset keys, dependency
selections, partitions, and schedules remain unchanged. Each selected model
retains its materialisation and owned test results. Skipped models and tests
do not report success. Successful siblings remain visible when a model fails.

Assets with explicit retry, runtime, cron, freshness, or isolated-write policies
keep their individual runners. Different resources, partitions, or capability
overrides form separate groups. If grouping would create an execution cycle,
the graph keeps individual runners. A targeted full refresh also uses individual
runners so that `--full-refresh` cannot apply to sibling models.

A group is one Dagster step, so step-level retries repeat its selected models.
The group's Dagster op name differs from the old per-model op names. Set
`DBT_GROUP_EXECUTION=false` to retain legacy per-op run configuration. Ordinary
asset selections and schedules need no changes.

Attached checks execute with their owning assets, as on the individual path.
Check-only selections are rejected before provider execution.

## Auto-Configuration

This package is **fully auto-configured**:

| Feature               | How It Works                                                            |
| --------------------- | ----------------------------------------------------------------------- |
| **Project Discovery** | Auto-discovers `dbt_project.yml` in workspace via `find_dbt_projects()` |
| **Asset Specs**       | Automatically creates asset specs from dbt models                        |
| **Lineage Events**    | Emits lineage events during model execution                             |
| **Auto-Compile**      | Compiles dbt on startup via post_start hook                             |

### Discovery Locations

If `DBT_PROJECT_DIR` is set, it is used before discovery.

The discovery module searches these paths in order:

1. `workflows/transforms/dbt/`

## Usage

### CLI Commands

```bash
# Compile dbt project
phlo dbt compile

# Run dbt in the active orchestrator service container
phlo dbt run

# Run host dbt directly
phlo dbt test --local
```

When `.phlo/` exists, `phlo dbt compile|run|test` executes inside the active orchestrator's
execution service by default. Use `--local` when you explicitly want host dbt. Successful
`phlo dbt run` commands also import manifest asset lineage into the configured lineage sink
when one is installed.

### Programmatic

```python
from phlo_dbt.discovery import find_dbt_projects, get_dbt_project_dir

# Find all dbt projects in workspace
projects = find_dbt_projects()

# Get the active dbt project directory
project_dir = get_dbt_project_dir()
```

## Entry Points

- `phlo.plugins.assets` - Provides `DbtAssetProvider` for asset specs
- `phlo.plugins.cli` - Provides `dbt` CLI commands
