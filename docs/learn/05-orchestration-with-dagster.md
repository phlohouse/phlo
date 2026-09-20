# Orchestration with Dagster

An orchestration system turns separate scripts into a visible graph of work. This post explains why Phlo models work as assets, how partitions, schedules, sensors, and checks fit together, and what a Dagster run gives you when something fails.

## The problem from first principles

A task says what to do once. A data asset says what result should exist and what other results it depends on. That difference matters when you need to rerun one date, understand downstream impact, or tell whether a table is current.

Without an orchestration record, a successful terminal command is weak evidence. You may not know which code version ran, which partition it used, which steps completed, or whether quality checks passed. A graph and run record make the execution inspectable.

Time also has two meanings. A partition describes the slice of data that an asset processes. A schedule describes when the platform should ask for a run. A sensor reacts to a condition or event. A backfill asks the orchestrator to process a range of partitions. Keeping these ideas separate makes a pipeline easier to reason about.

The asset graph is valuable because it represents dependencies rather than a list of commands. If a model depends on an ingestion asset, the graph can show the dependency, choose an upstream selection, and retain the run context for both. When a partition fails, the graph helps you decide whether to rerun that partition, its downstream assets, or the whole range.

That graph also gives the project a shared vocabulary. People can discuss the `dlt_events` asset and its `2025-01-15` partition instead of comparing shell history. A schedule can request a run without hiding the asset that the run changes. A sensor can react to an event without becoming a second, invisible scheduler. These distinctions reduce accidental duplication.

## How Phlo approaches it

`phlo-dagster` translates Phlo asset specifications into Dagster definitions. `phlo materialize` launches Dagster assets through the configured container backend. `phlo backfill` launches one Dagster run per partition and can execute several in parallel. The Dagster UI launches runs from the asset graph.

An asset can declare a daily partition, a `cron` schedule, or provider-specific checks. Ingestion assets can use `cron="0 */6 * * *"` to request a run every six hours. Sensors can promote audited WAP branches, trigger table maintenance, or send alerts for failed runs. The configured sensor acts on its condition, while Dagster keeps the run history.

Asset checks make quality visible beside the asset. A blocking check prevents a run from being treated as successful. A warning records an issue while allowing the run to finish. Dagster shows the check result, the materialisation, the partition, and the step log together.

The run record is also a handoff between people. The person who writes an asset may not be the person who investigates a failure. A visible run gives the investigator a partition, start and end state, step boundaries, logs, and checks without asking the author to reconstruct the terminal session. This is why an asset is more useful than a task name alone.

When you work through the UI, look at the asset before the run and the run before the service logs. The asset tells you what should exist. The run tells you what Dagster attempted. The step log tells you where execution stopped. That order keeps an infrastructure symptom from obscuring a data or dependency problem.

When you work through the UI, look at the asset before the run and the run before the service logs. The asset tells you what should exist. The run tells you what Dagster attempted. The step log tells you where execution stopped. That order keeps an infrastructure symptom from obscuring a data or dependency problem.

Provider command groups are not the normal run path. `phlo dbt`, `phlo sling`, `phlo airbyte`, and similar commands call their providers directly for inspection or debugging. They do not create the standard Dagster run record, lineage, or asset-check results.

## Try it

Open the Dagster UI:

```text
http://localhost:10006
```

Select **Assets** and search for `dlt_events`. The asset view shows its partitions and materialisation state. Select **Runs** to see the run created by the first-pipeline tutorial and open its step log.

Launch one partition from the CLI:

```bash
phlo materialize dlt_events --partition 2025-01-15
```

Select several assets with Dagster's selector syntax:

```bash
phlo materialize --select "tag:bronze"
```

Use an asset key when you know the exact asset:

```bash
phlo materialize --select "dlt_events"
```

Backfill a range with parallel execution:

```bash
phlo backfill dlt_events --start-date 2025-01-01 --end-date 2025-01-14 --parallel 4
```

Use `--dry-run` on either command when you want to see what would be executed without launching runs. The Dagster UI remains the best place to inspect the resulting run, partition, step log, and asset checks.

## Mental model to keep

- An asset describes a result and its dependencies.
- A partition bounds the data processed by a run.
- A schedule requests work at a time.
- A sensor requests work when a condition is met.
- A Dagster run is the record of what actually happened.
- An asset check turns a data expectation into visible run evidence.

## Where this goes wrong

- **A run is launched with a provider CLI.** The provider may succeed while Dagster has no run record, so use `phlo materialize` for normal execution.
- **A backfill overwhelms the source.** Lower `--parallel` or add `--delay` to control concurrent work.
- **A partition is missing from the asset view.** Check that the asset is partitioned and that the date is valid before rerunning it.
- **A check fails after the table is written.** Open the Dagster asset check and run record to distinguish a data issue from an orchestration issue.
- **A schedule does not fire.** Inspect the Dagster daemon and the schedule declaration before changing the asset function.

## Next

Continue with [Transformation with dbt](06-transformation-with-dbt.md) to add SQL models to the asset graph. For the complete selector and schedule explanation, read [Assets, partitions, and schedules](../concepts/assets-partitions-and-schedules.md).
