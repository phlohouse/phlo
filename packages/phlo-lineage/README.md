# phlo-lineage

Row-level data lineage tracking for Phlo.

## Description

Tracks data lineage at the row level, enabling you to trace individual records back through transformations to their source. Integrates with DLT and dbt pipelines.

## Installation

```bash
uv add phlo-lineage
# or
phlo plugin install lineage
```

## Configuration

| Variable         | Default | Description                      |
| ---------------- | ------- | -------------------------------- |
| `LINEAGE_DB_URL` | -       | PostgreSQL DSN for lineage store |

## Auto-Configuration

Auto-wired when `LINEAGE_DB_URL` is set:

| Feature               | How It Works                                                  |
| --------------------- | ------------------------------------------------------------- |
| **Hook Registration** | Receives `lineage.edges` events via HookBus                   |
| **Event Capture**     | Automatically captures lineage from `@phlo_ingestion` and dbt |
| **Row ID Injection**  | `_phlo_row_id` column auto-injected during ingestion          |

> **Note:** If `LINEAGE_DB_URL` is not configured, lineage events are logged but not persisted.

### Event Flow

```
Ingestion/Transform → emit lineage.edges → LineageHookPlugin → PostgreSQL
```

## Usage

### CLI Commands

```bash
# Query lineage for a table
phlo lineage show bronze.users

# Export lineage graph
phlo lineage export --format json
```

### Programmatic

```python
from phlo_lineage import get_lineage

# Get upstream lineage for a table
upstream = get_lineage("gold.fct_orders", direction="upstream")

# Get downstream lineage
downstream = get_lineage("bronze.raw_events", direction="downstream")
```

## Entry Points

- `phlo.plugins.cli` - Provides `lineage` CLI commands
- `phlo.plugins.hooks` - Provides `LineageHookPlugin` for event capture
