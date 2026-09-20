# Add quality checks

This guide adds row-level Pandera validation and provider-neutral table checks to an ingestion asset, then shows where a failed check appears.

## Before you start

- You have a project created with `phlo init`, a running stack, and an ingestion asset such as `dlt_events`.
- `phlo-pandera` is installed through `phlo[defaults]`, and the asset returns a DataFrame that has the columns used by the checks.
- You can open the Dagster UI at `http://localhost:10006` to inspect asset-check events.

## 1. Constrain the batch schema

Create or update a Pandera model in `workflows/schemas/events.py`. These constraints run while the ingestion batch is in memory and also provide the table schema used by the ingestion provider.

```python
from __future__ import annotations

import pandera.pandas as pa


class EventSchema(pa.DataFrameModel):
    event_id: str = pa.Field(nullable=False)
    value: int = pa.Field(ge=0)
    status: str = pa.Field(isin=["open", "closed"], nullable=False)
```

`ge=0` rejects negative values, `isin` limits the permitted status values, and `nullable=False` rejects missing values. A failing field check is recorded as a failed validation asset check.

## 2. Register a callable check on ingestion

Pass a callable through `quality_checks` when the rule needs Python logic that is specific to the incoming batch. Return `None` for a pass and a message string for a violation.

```python
import pandas as pd
import phlo

from workflows.schemas.events import EventSchema


def no_future_events(frame: pd.DataFrame) -> str | None:
    if (frame["event_date"] > pd.Timestamp.utcnow().date()).any():
        return "event_date contains a future date"
    return None


@phlo.ingest.dlt(
    table_name="events",
    unique_key="event_id",
    group="events",
    validation_schema=EventSchema,
    quality_checks=[no_future_events],
)
def events(partition_date: str):
    return load_events(partition_date)
```

The decorator creates an asset check named from the callable, evaluates it after staging, and includes the returned string in the CLI and Dagster failure metadata.

| Argument | Effect |
| --- | --- |
| `quality_checks=[...]` | Calls each function with the staged pandas DataFrame. |
| `validate=True` | Runs the Pandera validation path. |
| `strict_validation=True` | Makes a failed validation or callable check blocking. |
| `strict_validation=False` | Records failed checks as warnings and permits the run to continue. |

## 3. Attach neutral rules to an asset

Use `@phlo.quality.rules` for rules that query a materialised table rather than inspect the ingestion DataFrame. The decorated function is an asset definition, and its return value is not the rule input. The `table` argument identifies the table evaluated by the provider.

```python
import phlo
from phlo.quality_rules import accepted_values, not_null, unique


@phlo.quality.rules(
    table="raw.events",
    rules=[
        not_null("event_id"),
        unique("event_id"),
        accepted_values("status", ["open", "closed"]),
    ],
    blocking=False,
)
def events_quality():
    return None
```

The rules provider turns these declarations into a quality asset check, and the orchestrator executes that check when the generated quality asset is materialised. Use the exported neutral helpers if your project imports them at the top level.

| Rule | Checks |
| --- | --- |
| `not_null("column")` | No null values in the named column. |
| `unique("column")` | No duplicate values in the named column or column tuple. |
| `accepted_values("column", values)` | Every value belongs to the supplied list. |
| `range_between("column", min_value, max_value)` | Values stay within the inclusive bounds supplied. |
| `freshness("column", hours=24)` | The newest timestamp is within the allowed age. |

## 4. Choose blocking behaviour

Keep `strict_validation=True` when the table must not be published after a failed check. Set it to `False` when the check is advisory and the run should produce a warning while retaining the result for review.

```python
@phlo.ingest.dlt(
    table_name="events",
    unique_key="event_id",
    group="events",
    validation_schema=EventSchema,
    strict_validation=False,
    quality_checks=[no_future_events],
)
def events_with_warning(partition_date: str):
    return load_events(partition_date)
```

With strict validation, Dagster records a failed blocking `AssetCheckResult` and the materialisation fails. With non-strict validation, Dagster records the check with warning severity and the materialisation can finish.

## 5. Materialise and inspect the result

Launch a Dagster run with `phlo materialize` using the asset's positional name and a known partition:

```bash
phlo materialize dlt_events --partition 2025-01-15
```

A passing run ends with `Successfully materialized dlt_events`. A failed check prints `Domain quality check failed` and the returned violation message, while Dagster shows the failed asset check on the asset page.

## Verify

Open `http://localhost:10006`, select the `dlt_events` asset, and open the **Checks** pane. A successful run shows a passed check. A deliberately invalid row shows a failed check with the check name and violation message.

## Related

- [Ingest data](ingest-data.md) for the validation schema and ingestion asset.
- [Quality checks](../reference/quality-checks.md) for provider classes and decorator parameters.
- [Write-audit-publish](../concepts/write-audit-publish.md) for the publish gate that uses check results.
