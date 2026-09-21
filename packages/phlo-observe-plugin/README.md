# phlo-observe-plugin

Canonical `phlo-observe` integration for Phlo.

The observer SDK packages are currently sourced from their release branches
while their package distributions are prepared. Repository development installs
them through Phlo's `dev` dependency group. A published `phlo` installation
remains URL-free; enable the integration after installing compatible observer
SDK distributions or source packages in your environment.

## What it provides

- A hook plugin that translates pipeline events into canonical logs, metrics,
  and operation events while preserving correlation.
- A Dagster extension that keeps physical execution IDs distinct from logical
  retry-chain IDs.
- An opt-in `phlo-observer` service for ingest and query. ClickStack remains
  Phlo's default observability backend.

Observability is fail-open: unavailable drains or observer services never stop
pipeline work.

## Use

Install the package, then enable the observability profile when a standalone
observer is wanted:

```bash
pip install phlo-observe-plugin
phlo services start --profile observability
```

Set `OBSERVE_HTTP_ENDPOINT` to the observer's `/v1/events` endpoint. For local
development, the service defaults to unauthenticated ingest. For hardened
deployments, configure matching `OBSERVE_HTTP_TOKEN` and
`PHLO_OBSERVER_INGEST_TOKENS` values.

`PHLO_OBSERVE_PRETTY=true` enables the concise terminal drain. The existing
hook bus API remains compatible; its events are translated rather than
replaced.
