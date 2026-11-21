# phlo-openmetadata

OpenMetadata integration for Phlo.

## Description

Syncs table metadata, lineage, and quality check results to OpenMetadata for data governance and discovery.

## Installation

```bash
uv add phlo-openmetadata
# or
phlo plugin install openmetadata
```

## Configuration

| Variable                             | Default               | Description                |
| ------------------------------------ | --------------------- | -------------------------- |
| `OPENMETADATA_HOST`                  | `openmetadata-server` | OpenMetadata server host   |
| `OPENMETADATA_PORT`                  | `8585`                | OpenMetadata API port      |
| `OPENMETADATA_HEAP_OPTS`             | `-Xmx512m -Xms512m`   | OpenMetadata server heap for bundled stack |
| `OPENMETADATA_ES_JAVA_OPTS`          | `-Xms512m -Xmx512m`   | Elasticsearch heap for bundled stack |
| `OPENMETADATA_USERNAME`              | `admin`               | Admin username             |
| `OPENMETADATA_PASSWORD`              | `admin`               | Admin password             |
| `OPENMETADATA_VERIFY_SSL`            | `false`               | Verify SSL certificates    |
| `OPENMETADATA_SYNC_ENABLED`          | `true`                | Enable automatic sync      |
| `OPENMETADATA_SYNC_INTERVAL_SECONDS` | `300`                 | Min interval between syncs |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature               | How It Works                                                     |
| --------------------- | ---------------------------------------------------------------- |
| **Hook Registration** | Receives `lineage.edges`, `quality.result`, `publish.end` events |
| **Lineage Sync**      | Automatically syncs lineage edges to OpenMetadata                |
| **Quality Results**   | Syncs quality check results as test cases                        |
| **Table Metadata**    | Syncs published tables with documentation                        |

### Event Flow

```
Pipeline Events → HookBus → OpenMetadataHookPlugin → OpenMetadata API
```

## Usage

### CLI Commands

```bash
# Manually sync all tables
phlo openmetadata sync

# Sync specific table
phlo openmetadata sync --table bronze.users
```

### Programmatic

```python
from phlo_openmetadata.client import OpenMetadataClient

client = OpenMetadataClient()
client.sync_table_metadata("bronze.users")
```

## Entry Points

- `phlo.plugins.cli` - Provides `openmetadata` CLI commands
- `phlo.plugins.hooks` - Provides `OpenMetadataHookPlugin` for sync
