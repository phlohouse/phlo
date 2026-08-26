# SaaS Product Analytics lakehouse

This standalone Phlo lakehouse replays a paginated SaaS API and an account-plan
CSV into Iceberg, then builds six product analytics tables through dbt.

## Run it

Start the deterministic replay API in one terminal. The baseline fixture has no
`experiment_variant` field and deliberately returns one retryable HTTP 429:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run python scripts/replay_server.py --events-file events-v1.json
```

In a second terminal, start the lakehouse and publish each asset in dependency
order:

```bash
uv run pytest -q
uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor
uv run phlo materialize dlt_saas_account_plans --partition 2025-01-02
uv run phlo materialize dlt_saas_events --partition 2025-01-02
uv run phlo materialize flattened_events --partition 2025-01-02
uv run phlo materialize sessions --partition 2025-01-02
uv run phlo materialize activation --partition 2025-01-02
uv run phlo materialize retention --partition 2025-01-02
uv run phlo materialize feature_adoption --partition 2025-01-02
uv run phlo materialize release_impact --partition 2025-01-02
```

Wait for each command's WAP report in `.phlo/wap-reports/` to reach
`promoted` before launching the next dependent asset. A completed service-start
command is the readiness boundary: it creates Nessie's bootstrap history before
the first WAP branch is launched.

## What to observe

The deterministic replay returns eight distinct events across three API pages,
retries one 429 response, and retains nullable `feature` and
`experiment_variant` fields. The published raw tables contain three plans and
eight events. The curated outputs are `flattened_events`, `sessions`,
`activation`, `retention`, `feature_adoption`, and `release_impact`.

```bash
uv run phlo trino --execute 'SELECT count(*), count(DISTINCT event_id) FROM iceberg.raw.saas_events'
uv run phlo catalog tables
```

The baseline produces four ordered sessions, activates only `acc-1`, and yields
two day-zero accounts plus one day-one retained account in the 2025-01-01
cohort. `boards` has two feature events and `automations` has one.

## Exercise schema evolution

Restart the replay API with the evolved fixture and materialize events plus the
downstream assets again:

```bash
uv run python scripts/replay_server.py --events-file events-v2.json
uv run phlo materialize dlt_saas_events --partition 2025-01-02
```

The merge still contains eight distinct event IDs, while one row now has
`experiment_variant = 'treatment'`. This demonstrates a nullable source field
appearing without duplicating or dropping existing events.

## Exercise fail-closed publication

Run the replay API with `failures/invalid_event.json`, then materialize the
events asset. Its unsupported event type fails the blocking Pandera contract.
Dagster and the WAP report retain the failure evidence, while the published
catalog remains at the last valid eight-row snapshot.

Three stopped schedules model hourly events, daily cohorts, and weekly full
publication. Invalid replay fixtures under `generated-data/failures/` exercise
the blocking quality path; a failed WAP report retains audit evidence while
leaving the published catalog unchanged.

The illustrated [end-to-end case study](docs/saas-product-analytics-e2e.md)
explains the architecture, analytics results, quality checks, schema evolution,
and publication lifecycle.
