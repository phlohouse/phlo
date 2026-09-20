# Govern a dataset

Use this guide when you need to declare ownership, consumers, and service-level expectations for a table, check governance readiness, inspect its Dataset projection, and apply an authorised transition.

## Before you start

You need a Phlo project with `phlo`, `phlo-core-plugins`, and the provider that owns the table. The durable Dataset store requires `phlo-postgres`, or you can use `--store-mode memory` for an explicit local test.

## 1. Declare the governance metadata

Create or update a workflow module such as `workflows/governed_table.py`. The decorators below register governance metadata and do not replace the provider asset that materialises the table.

```python
import phlo
from phlo.contracts import Consumer, SLA


@phlo.contract(
    table="dlt_events",
    owner="data-platform",
    consumers=[Consumer(name="analytics", usage="daily reporting")],
    sla=SLA(freshness_hours=24),
)
@phlo.publish(table="dlt_events", audience=["analytics"], owner="data-platform")
def governed_events():
    """Describe the dlt_events governance surface."""
```

These declarations join the governance metadata for the table. Keep the table name aligned with the provider asset.

## 2. Check readiness

Run the governance check from the project root.

```bash
phlo governance check
```

The command prints `Governance check passed` when the declarations produce no blocking warnings. It exits with status 1 when a check fails, so you can use it as a CI gate.

## 3. Inspect the Dataset projection

List the canonical projections, then show one Dataset by its ID.

```bash
phlo dataset list
phlo dataset show dlt_events
```

`phlo dataset list` requires a durable dataset state provider by default. Use `--store-mode memory` for an explicit process-local test, and do not treat that state as durable.

## 4. Apply a transition

Review the current state and choose one action from the transition contract. The available actions are `claim`, `review`, `promote`, `reject`, `publish`, and `retire`.

Before applying a transition, confirm the Dataset ID, current state, owner, and intended action. Transitions are authorised and audited writes, so use an explicit `--action-id` when a caller needs replayable idempotency.

```bash
phlo dataset transition dlt_events publish --expected-state draft --action-id publish-dlt-events-1
```

The transition command supports `--json`, `--store-mode`, `--expected-state`, `--owner`, and `--action-id`. Use the exact state returned by `phlo dataset show` for compare-and-set protection.

## 5. Materialise through Dagster

Run the provider asset through Dagster after the metadata is ready.

```bash
phlo materialize dlt_events --partition 2025-01-15
```

The Dagster run records asset metadata and checks. Observatory and the browser-safe governance read model expose the owner, consumers, SLA, access policies, and readiness evidence.

## Verify

Run `phlo governance check` again and inspect the Dataset projection with `phlo dataset show dlt_events`. Then open the Dagster UI at `http://localhost:10006` and select the asset to inspect its latest materialisation and checks.

## Related

- [Governance and datasets](../concepts/governance-and-datasets.md)
- [Auth and access](../reference/auth-and-access.md)
- [Configuration tenant scope](../reference/configuration.md#tenant-scope)
