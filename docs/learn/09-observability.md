# Observability

Observability answers a practical question: what happened, and how do you know? This post gives you four signals to use together, shows where Dagster and Observatory fit, and explains how logs, metrics, lineage, evidence, and alerts shorten the path from failure to understanding.

## The problem from first principles

A process can be running while its data is stale. A run can be green while a consumer-facing check is warning. A service can be healthy while one asset is failing. Looking at one dashboard or one log stream gives you only one slice of the system.

The useful signals are status, logs, metrics, and lineage. Status tells you whether a run or service is healthy. Logs explain what a step said while it ran. Metrics show quantities and trends. Lineage shows how a result depends on upstream assets and columns. Run evidence adds a durable account of the run, its events, and its report.

These signals answer different questions. Status is good for deciding where to look first. Logs are good for the immediate error. Metrics reveal drift and cost. Lineage reveals impact. Evidence helps you explain what happened after the process that produced it has ended.

Use the signals in that order when a user reports a problem. First identify the asset and partition. Then open its Dagster run and failed step. Read service logs only when the run points to an infrastructure problem. Check lineage when you need to know who is affected. Compare metrics and evidence when the immediate failure is understood but the system needs a longer-term explanation.

## How Phlo approaches it

Dagster is the first place to inspect an asset run. The Runs view shows status, partition, steps, and logs. The asset page shows materialisations and checks. `phlo status` reads asset and service state, while `phlo logs` reads infrastructure service logs.

Observatory provides a user-facing view of platform and run information. The `phlo-observatory` package supplies the UI and routes. The optional `phlo-observe-plugin` package translates events for its Observatory service. Run evidence can use local `.phlo/run-evidence.sqlite` storage or PostgreSQL when `PHLO_RUN_EVIDENCE_DB_URL` is set.

The optional stack is useful when several people need the same operational view. OpenTelemetry carries events, Alloy routes telemetry, Loki stores logs, Prometheus stores metrics, and Grafana presents dashboards. ClickStack provides a separate telemetry view. These components add collection and presentation. They do not replace the Dagster run record.

Choose the smallest observability surface that answers the question. The Dagster UI may be enough for a failed partition. Status and logs may be enough for a local service issue. Metrics and dashboards become useful when you need to compare runs across time. Lineage and durable evidence become important when the result affects several consumers or needs review.

The four signals reinforce each other. A stale status points to a run or schedule. The run log identifies the failing step. Lineage identifies downstream impact. Metrics show whether the same failure is recurring. Evidence records the decision and its supporting facts. No single signal has to carry the whole investigation.

Lineage is populated by asset dependencies and can include column-level relationships when the relevant lineage package is enabled. `phlo lineage` can show a dependency tree, export a graph, inspect impact, and query column-level lineage.

The optional observability profile adds OpenTelemetry, Alloy, Loki, Prometheus, Grafana, ClickStack, and alerting packages. Alerts can be inspected with `phlo alerts list`, `phlo alerts status`, and `phlo alerts test`. Alert destinations use `PHLO_ALERT_*` settings.

## Try it

Start with the Dagster run:

```text
http://localhost:10006
```

Open **Runs**, select the latest `dlt_events` run, and read the step log. Then inspect the asset page for its materialisation and checks.

Use Phlo status to separate asset state from service state:

```bash
phlo status --assets
phlo status --services
```

Read recent service logs:

```bash
phlo logs --service dagster --tail 100
```

Inspect lineage after an asset has run:

```bash
phlo lineage status
phlo lineage show dlt_events
```

Check the alert configuration:

```bash
phlo alerts list
phlo alerts status
```

Send a test only when you intend to notify configured destinations:

```bash
phlo alerts test --severity warning
```

The observability stack is optional. Follow [Add observability](../guides/add-observability.md) before configuring telemetry services, and use [Collect evidence](../guides/collect-evidence.md) when you need an exportable evidence pack.

## Mental model to keep

- Start with the Dagster run that represents the work.
- Use logs to explain a step, not to infer whether the asset is current.
- Use metrics to see trends and cost.
- Use lineage to understand impact and ownership.
- Use evidence when the run must be explained later.
- Alerts are a delivery path for signals, not a replacement for investigation.

## Where this goes wrong

- **The first search is raw container output.** The Dagster run usually gives the asset, partition, step, and check context in one place.
- **Lineage is empty.** Run the asset through Dagster and confirm the lineage store is configured.
- **An alert test causes confusion.** `phlo alerts test` sends to configured destinations, so inspect the destination list before testing.
- **Metrics exist without a baseline.** Compare a run with earlier runs before treating one duration or row count as a trend.
- **Evidence is assumed to be durable.** Confirm whether the environment uses `.phlo/run-evidence.sqlite` or `PHLO_RUN_EVIDENCE_DB_URL`.

## Next

Continue with [Incidents and debugging](10-incidents-and-debugging.md) to turn these signals into a response flow. Read [Monitor and debug](../guides/monitor-and-debug.md), [Evidence, audit, and compliance](../concepts/evidence-audit-and-compliance.md), and [Add observability](../guides/add-observability.md) for task-focused procedures.
