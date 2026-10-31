# phlo-core-plugins

Core plugins for Phlo (quality checks and source connectors).

## Description

Provides built-in quality check plugins and source connectors. These are registered via entry points and auto-discovered at runtime.

## Installation

```bash
pip install phlo-core-plugins
# or included with phlo by default
```

## Auto-Configuration

This package is **fully auto-configured**:

| Feature               | How It Works                                            |
| --------------------- | ------------------------------------------------------- |
| **Quality Checks**    | Auto-registered via `phlo.plugins.quality` entry points |
| **Source Connectors** | Auto-registered via `phlo.plugins.sources` entry points |

## Quality Check Plugins

| Check              | Entry Point            | Description              |
| ------------------ | ---------------------- | ------------------------ |
| `null_check`       | `phlo.plugins.quality` | Validates no NULL values |
| `uniqueness_check` | `phlo.plugins.quality` | Validates unique values  |
| `range_check`      | `phlo.plugins.quality` | Validates value ranges   |
| `regex_check`      | `phlo.plugins.quality` | Validates regex patterns |
| `freshness_check`  | `phlo.plugins.quality` | Validates data freshness |

## Source Connector Plugins

| Connector  | Entry Point            | Description                |
| ---------- | ---------------------- | -------------------------- |
| `rest_api` | `phlo.plugins.sources` | Generic REST API connector |

## Usage

### Quality Checks

```python
import phlo
from phlo.quality import NullCheck, UniqueCheck

@phlo.quality.pandera(
    table="bronze.users",
    checks=[
        NullCheck(columns=["id"]),
        UniqueCheck(columns=["email"]),
    ],
)
def validate_users():
    pass
```

### Source Connectors

```python
import phlo

@phlo.ingest.dlt(
    name="api_data",
    source="rest_api",  # Uses rest_api connector
    destination="bronze.api_data"
)
def ingest_api():
    return {"client": {"base_url": "https://api.example.com"}}
```

## Entry Points

- `phlo.plugins.quality` - Quality check plugins
- `phlo.plugins.sources` - Source connector plugins
