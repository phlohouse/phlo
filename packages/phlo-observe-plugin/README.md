# phlo-observe-plugin

Canonical `phlo-observe` integration for Phlo.

The package installs the published `phlo-observe` SDK and its core and query
dependencies from PyPI.

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
phlo services init --profile observability
phlo services start
phlo services start --profile observability
```

Set `OBSERVE_HTTP_ENDPOINT` to the observer's `/v1/events` endpoint. For local
development, the service defaults to unauthenticated ingest. For hardened
deployments, configure matching `OBSERVE_HTTP_TOKEN` and
`PHLO_OBSERVER_INGEST_TOKENS` values.

The observability profile enables the concise terminal drain. Set
`PHLO_OBSERVE_PRETTY_VERBOSE=true` to include secondary diagnostic events and
the full framework log stream. Set `PHLO_OBSERVE_PRETTY=false` to keep
canonical events out of the terminal. Store either setting in the top-level
`env` block in `phlo.yaml`, then regenerate the service configuration:

```yaml
env:
  PHLO_OBSERVE_PRETTY: "true"
  PHLO_OBSERVE_PRETTY_VERBOSE: "false"
```

The existing hook bus API remains compatible. The plugin translates its
events rather than replacing them.
