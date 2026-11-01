# Orchestration performance

The changes for [#1251](https://github.com/phlohouse/phlo/issues/1251),
[#1252](https://github.com/phlohouse/phlo/issues/1252),
[#1253](https://github.com/phlohouse/phlo/issues/1253) and
[#1254](https://github.com/phlohouse/phlo/issues/1254) remove repeated dbt
documentation work, reuse validated parsing state, keep a shared Dagster code
location and reuse the daemon's PostgreSQL connections.

## Behaviour and compatibility

`DbtTransformer.run_transform()` builds without generating a catalogue by
default. Pass `parameters={"generate_docs": True}` to retain explicit docs
generation, or combine it with `skip_build=True` for docs-only execution.
Build materialisations, lineage and checks still use the build's own manifest
and results, including results from a failed build.

Run-specific target directories remain isolated. Only `partial_parse.msgpack`
is copied through `target/.phlo/partial_parse.msgpack`. dbt validates compatibility
with the project, profiles, target and variables. Atomic replacement prevents
concurrent writers from publishing a partial file. The last complete cache wins;
incompatible concurrent builds can reduce cache hits but cannot reuse each other's
manifests or results. Removing the cache is safe and causes a cold parse.

Generated stacks add `dagster-code`, using the existing Dagster image and
bootstrap. Webserver and daemon share its reloadable gRPC location under the
existing location name `phlo_dagster.framework.definitions`. The gRPC listener
binds only to a dedicated internal network alias, not the shared data network
or a published host port. The code service retains data-service access, scoped
orchestration credentials and host UID handling. Reload the code location in
Dagster after editing workflows. Periodic workspace refresh no longer imports
the project again; it is not a substitute for deliberate code reload.

The daemon owns its instance and calls Dagster's existing storage optimisation
API. Workers and code servers reconstruct independent instances, so they do not
inherit its pools. Polling intervals, transaction isolation and run launcher
selection are unchanged. Statement timeout remains unlimited. This entrypoint
uses Dagster's internal CLI runner within the package's pinned 1.13 minor range;
upgrading that range requires the lifecycle check below.

For an existing project, review `phlo services upgrade --dry-run` using the
[upgrade procedure](../guides/upgrade-phlo.md), including generated workspace,
new companion service and restart order. Do not use `services init --force`
as an upgrade mechanism. Rollback restores the previous generated workspace,
Compose files and image together. No database schema migration is introduced.

## Disposable measurements

Baseline: `a416b1b082a`, with candidate changes in this branch. Measurements ran
on Linux x86-64 with Python 3.12.3, Dagster 1.13.26, dbt Core 1.12.5 and DuckDB 1.5.1.
CPU-limited, low-priority scopes protected an unrelated running soak. PostgreSQL
used an owned `postgres:18-alpine` container limited to 0.15 CPU and 192 MiB.
No deployed application was changed or instrumented.

| Workload | Baseline | Candidate |
| --- | ---: | ---: |
| Five real isolated dbt model builds, docs invocations | 5 | 0 |
| Same builds, full parses | 5 | 1 |
| Same builds, warm build-cache hits | 0 | 4 |
| Two workspace refreshes, 60 seconds apart, project imports including startup | 3 | 1 |
| Same idle workspace interval, process-tree CPU seconds after startup | 5.561 | 2.077 |
| Five immediate workspace refreshes, process-tree CPU seconds after startup | 19.771 | 0.231 |
| Six idle daemon loops, new PostgreSQL sessions over 65 seconds | 134 | 0 |
| Same interval, PostgreSQL container CPU seconds | 2.795 | 0.207 |

The workspace experiment uses a real minimal Dagster definitions file, the
daemon's gRPC registry and real workspace refreshes. It compares managed
Python-file loading with `dagster code-server start` and a gRPC workspace.
The idle intervals took 146.608 and 120.505 wall seconds respectively because
managed refreshes also waited for replacement servers. These are component
measurements, not whole-stack CPU percentages. Each workspace and PostgreSQL
comparison has one sample per variant. The PostgreSQL workload has an empty
workspace and all six heartbeat kinds, isolating the daemon as a connection
source without changing its polls. The webserver and workers are not attributed
by that experiment.

The five dbt wall-time samples ranged from 34.40 to 49.61 seconds before and
15.78 to 56.72 seconds after, with medians of 36.12 and 21.26 seconds. Host load
varied; work counts are stronger evidence than those timings. DuckDB docs reused
parsing in this fixture, unlike the original Trino observation. This comparison
does not prove a ten-to-one parse reduction, Trino-specific wall-time savings,
whole-stack peak memory savings or long-duration production reliability.

Raw logs, JSON samples and disposable experiment drivers are retained as review
artifacts in the implementation thread. The checks below retain the behavioural
contracts in the repository rather than asserting host-specific timings.

## Repeat the contract checks

Run from the repository root after `make setup`:

```bash
uv run --locked pytest packages/phlo-dbt/tests/test_transform.py
uv run --locked pytest -m integration packages/phlo-dbt/tests/test_integration_dbt.py
uv run --locked pytest -m integration packages/phlo-dagster/tests/test_code_server_lifecycle.py
```

The dbt integration checks verify real output values, compatible reuse, changed
variables, models, profiles and WAP targets, explicit docs and artifact isolation.
The concurrent-build regression also covers one failed build.

The Dagster lifecycle check owns a bounded disposable PostgreSQL container.
`PHLO_TEST_POSTGRES_DSN`, if supplied, must name an empty disposable database;
the test deliberately terminates its idle sessions. It exercises sensor-triggered
execution, queued successful and failed jobs, code reload, code-server restart,
connection-loss recovery and low idle connection counts. It uses real gRPC and
the default run launcher, not a mocked launch path. The storage integration CI
group runs both real-provider modules.

A separate bounded Docker experiment verified that the code listener was
reachable on its private interface and refused connections on its shared data
interface. Generated Compose portability and identity are covered by parser and
render tests. These checks do not replace a complete generated-stack image
build, Windows runtime testing, release qualification or remote CI.
