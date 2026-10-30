# phlo-pandera

Data quality checks and validation for Phlo.

## Description

Define and execute data quality checks using the `@phlo.quality.pandera` decorator. Checks emit capability specs that adapters translate into orchestrator-native checks.

## Installation

```bash
pip install phlo-pandera
# or
phlo plugin install pandera
```

## Configuration

| Variable                 | Default | Description                    |
| ------------------------ | ------- | ------------------------------ |
| `PANDERA_CRITICAL_LEVEL` | `error` | Severity that blocks promotion |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature                 | How It Works                                              |
| ----------------------- | --------------------------------------------------------- |
| **Check Discovery**     | Quality workflows auto-discovered in `workflows/quality/` |
| **Event Emission**      | Emits `quality.result` events to HookBus                  |
| **Adapter Integration** | Checks translate into orchestrator-native checks via adapters |
| **Alerting**            | Failed checks auto-routed to alerting destinations        |

### Event Flow

```
@phlo.quality.pandera → QualityEventEmitter → quality.result → [Alerting, Metrics, OpenMetadata]
```

## Usage

### Defining Checks

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

### CLI Commands

```bash
# Run quality checks
phlo quality run --asset bronze.users

# List available checks
phlo quality list
```

## Built-in Checks

| Check              | Description                         |
| ------------------ | ----------------------------------- |
| `null_check`       | Validates column has no NULL values |
| `uniqueness_check` | Validates column values are unique  |
| `range_check`      | Validates values are within range   |
| `regex_check`      | Validates values match pattern      |
| `freshness_check`  | Validates data is recent            |

## Entry Points

- `phlo.plugins.cli` - Provides `pandera` CLI commands
- `phlo.plugins.quality` - Provides built-in check plugins
