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

Webserver and code-server health probes bypass Python's startup hooks.
The webserver still checks `/server_info`. The code probe loads the installed
gRPC health client directly and still requires the `DagsterApi` service to
report `SERVING`. The probe
finds dependencies relative to its Python executable in both locked-venv and
system installations. Check intervals, container timeouts, retries and normal
application startup are unchanged.

The normal Python startup hook imports `SupersessionWarning` from
`dagster_shared.utils.warnings`, where Dagster 1.13 defines the class it
re-exports. The same category remains suppressed, but unrelated Python commands
such as dbt no longer initialise Dagster just to install the warning filter.
Ordinary warnings and other `FutureWarning` categories remain visible.

The daemon owns its instance and calls Dagster's existing storage optimisation
API. Workers and code servers reconstruct independent instances, so they do not
inherit its pools. Polling intervals, transaction isolation and run launcher
selection are unchanged. Statement timeout remains unlimited. This entrypoint
uses Dagster's internal CLI runner within the package's pinned 1.13 minor range;
upgrading that range requires the lifecycle check below.
SQLAlchemy's checkout hook checks the daemon's psycopg2 connections before use
and replaces dead sessions without retrying application writes. This adds one
`SELECT 1` per checkout. Without it, a dropped idle connection could fail the
first scheduled run even after heartbeat recovery.

The observe extension omits its three run-status sensors when telemetry is
unconfigured or explicitly disabled. Dagster queries storage before invoking
the sensor callback, so an emission-only guard cannot avoid those queries.
Registration uses configuration intent rather than SDK or drain health.
Configured sensors retain their names, RUNNING defaults and Dagster cursor
semantics even when the SDK cannot initialise. Telemetry configuration changes
require a code-location reload. Container environment changes also require
recreating the code service through the normal configuration rollout.

Pandera quality groups share row count, built-in null counts and range statistics
in one SQL aggregate query. Uniqueness queries remain separate. Failure samples
remain bounded to twenty rows, and custom subclasses still execute their own
checks. The schema probe remains because column-count metadata and floating-point
NaN handling depend on it, including for count-only groups. This changes query
counts, not the number of declared checks or their severity thresholds.

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
| Six idle daemon loops, new PostgreSQL sessions over 65 seconds | 136 | 1 |
| Same interval, PostgreSQL container CPU seconds | 2.761 | 0.390 |

The workspace experiment uses a real minimal Dagster definitions file, the
daemon's gRPC registry and real workspace refreshes. It compares managed
Python-file loading with `dagster code-server start` and a gRPC workspace.
The idle intervals took 146.608 and 120.505 wall seconds respectively because
managed refreshes also waited for replacement servers. These are component
measurements, not whole-stack CPU percentages. Each workspace and PostgreSQL
comparison has one sample per variant. The PostgreSQL workload has an empty
workspace and all six heartbeat kinds, isolating the daemon as a connection
source without changing its polls. The candidate includes the connection
checkout check. The webserver and workers are not attributed by that experiment.

The five dbt wall-time samples ranged from 34.40 to 49.61 seconds before and
15.78 to 56.72 seconds after, with medians of 36.12 and 21.26 seconds. Host load
varied; work counts are stronger evidence than those timings. DuckDB docs reused
parsing in this fixture, unlike the original Trino observation. This comparison
does not prove a ten-to-one parse reduction, Trino-specific wall-time savings,
whole-stack peak memory savings or long-duration production reliability.

Raw logs, JSON samples and disposable experiment drivers are retained as review
artifacts in the implementation thread. The checks below retain the behavioural
contracts in the repository rather than asserting host-specific timings.

### Further query reductions

An ephemeral Dagster instance performed six run-status storage fetches over
two evaluation rounds with the prior disabled sensors, and none with the new
registration gate. Explicitly enabled sensors still performed six fetches.
After disabling and re-enabling definitions, saved cursors delivered success,
failure and cancellation events recorded during the gap. This verifies sensor
evaluation against isolated storage, not daemon CPU or a deployed reload.

Against `dd1312f67ab`, a disposable real DuckDB comparison produced identical
complete result metadata across eight cases: passing checks, missing columns,
multiple null checks, a custom subclass, count-only checks, all-missing columns,
empty input and bounded failure samples. A passing Unique + Null + Count group
used three statements instead of four. Two null checks plus a failing range
check used five instead of seven, including their failure-sample queries.
Count-only and empty-input cases retained two statements.

These are query counts, not Trino latency or whole-stack CPU measurements.
No new Trino JVM was started for this comparison because the VPS memory budget
was already constrained. The existing opt-in real Trino check remains available
for release validation. No live Nightscout data or services were changed.

### Manifest ownership and SQL allocation

Discovery now builds one manifest-scoped index of test ownership, check specs
and selectable names. Each asset runner retains its own immutable selector
tuple, including an empty tuple for assets without tests. Retries no longer
scan every manifest test. Reloading definitions builds a new index, and
federated projects keep separate indexes. Check identities still deduplicate
independently of selectable test names.

A disposable comparison against `d8194686648` discovered 100 assets with 724
tests, then called every production asset runner with a recording transformer
instead of invoking dbt. Across three rounds, runtime ownership resolutions
fell from 72,400 to zero. Discovery retained 724 resolutions. Runner CPU time
ranged from 0.552 to 0.601 seconds before and 0.011 to 0.014 seconds after.
This isolates Python orchestration overhead. It does not measure dbt execution,
Trino queries or whole-stack CPU savings.

Compiled-SQL excerpts now retain a bounded UTF-8 prefix while reading files in
16 Ki-character chunks. The reader still scans the complete file to report its
normalised original byte length and reject invalid UTF-8. Source precedence,
newline normalisation, truncation markers and path confinement are unchanged.
Manifest strings use the same chunked encoding. Negative byte limits retain
the existing slicing behaviour and do not provide a bounded-memory guarantee.

With a 256-byte excerpt limit, `tracemalloc` measured peak allocations of
2,102,639 bytes before and 88,147 bytes after for a 1 MiB compiled file.
For an 8 MiB file, peaks were 16,782,566 and 88,010 bytes. Imports were warmed
before tracing, and file creation was outside the measured interval. These
are Python allocation peaks for the reader, not process RSS or reduced file
I/O. Each size has one sample per variant.

A disposable public-API comparison matched 436 results against the baseline,
including Unicode, newline, limit, manifest fallback, missing-file and traversal
cases. Both versions rejected invalid UTF-8 after the retained prefix.

### Lineage persistence work

`record_asset_edges()` now owns one short-lived PostgreSQL connection for its
node and edge batches. Nodes still commit before edges, so a failed edge write
does not roll back successful nodes. The operation explicitly closes its
connection on success and failure. Standalone node writes and schema bootstrap
also close their owned connections. No global pool or database migration is
introduced, and lineage events retain their complete payloads.

Metadata and tags encode once per SQL batch rather than once per row. Edge
encoding remains after the node commit and success log, preserving changes to
the dictionaries between phases.

A recording-driver comparison against `d8194686648` submitted five events,
each containing five nodes and four edges with nonempty metadata and tags.
Event connections fell from ten to five, and JSON encodings fell from ninety
to twenty. Both versions made ten explicit commits and returned four edges
per event. Complete SQL and bound values matched for all ten batches. With a
cold schema cache, both versions added one separate existence-probe connection.
The candidate explicitly closed all six connections in that case.

This comparison measures driver calls and payload parity, not PostgreSQL CPU,
connection latency or transaction durability. The recording driver does not
simulate implicit transaction-context commits or eventual garbage collection
of the baseline's connections. Missing-schema migrations were not benchmarked.

The deeper pass passed 265 combined package, adapter and benchmark tests, with
one optional dbt-CLI test skipped and two integration tests deselected. After
review strengthened the federation regression and removed the private runner's
scan fallback, all 139 dbt package tests passed with the CLI on `PATH`, including
the disposable real DuckDB full-refresh check. Focused production typing, Ruff
lint, complexity, formatting, import contracts and Markdown links passed.
These local checks do not replace full CI or a deployed before-and-after sample.

### Deployed health-probe measurements

Five alternating invocations per variant against the running services measured
median child-process CPU of 1.213 seconds before and 0.147 seconds after for
the code probe. The webserver probe fell from 0.925 to 0.076 seconds. An empty
Python invocation used about one CPU-second with the image's startup hook,
versus 0.013 seconds with `-S`. These are process measurements, not RPC latency
or projected whole-stack savings.

Three-minute samples on a 1.75-CPU, 6-GiB development account measured total
idle project CPU of 38.622% before this branch, 15.918% with `0f7c1f92cd5`,
and 9.773% with the health-probe changes. Here 100% means one logical CPU.
Each variant has one idle window, with automation enabled and no active runs.
This is short-window deployment evidence, not long-duration soak qualification.
The health regression executes the packaged command against a real gRPC health
service and checks serving, unready, unknown and disconnected outcomes.

### Normal Python startup measurements

Five alternating isolated imports inside the deployed container measured
median process CPU of 0.654 seconds through `dagster`, versus 0.054 seconds
through `dagster_shared.utils.warnings`. The latter did not load `dagster`.
Both variants installed the same category filter. This measures startup work,
not a projected reduction in whole-stack CPU.

Three alternating `dbt --version` probes measured median process CPU of
2.850 versus 2.058 seconds and peak RSS of 147.2 versus 116.3 MiB. These are
command-startup measurements, not peak memory for a dbt build or the lakehouse.

Three alternating pairs of real Nightscout mart replays measured median
RUN_START-to-RUN_SUCCESS time of 37.882 seconds before this import change and
30.785 seconds after it. Median project-container CPU per replay fell from
51.582 to 38.401 CPU-seconds. The replay CPU interval includes Python startup,
definition discovery and adjacent container work. Scheduling was paused and
restored around the comparison. Both variants used the same retained sources,
partition label and five separate dbt invocations. All six runs retained five
materialisations and eleven passing checks.

The initial before-to-after mart checksum comparison failed because the first
replay refreshed marts that lagged the ingested source. Source-table fingerprints
were unchanged. A separate fresh baseline/candidate pair compared fingerprints
after each rebuild and matched all seven table schemas, counts and stable-field
checksums. Only clock-dependent device evaluation fields were excluded.
Those validation timings are not included in the three-pair medians.

The startup regression executes the packaged hook in a fresh interpreter and
requires ordinary Python startup not to load Dagster. It then imports Dagster's
warning category and checks that only supersession warnings are suppressed.

### Shared range statistics

Three alternating baseline/candidate comparisons against the running Trino
service used 721 retained glucose readings with count, null and range checks.
The candidate reduced queries from three to two, including the schema probe.
Median client time was 294 versus 222 milliseconds. Trino reported only four
versus three milliseconds of median query CPU, so this small workload does not
establish a whole-stack CPU saving.

All serialised check results matched across baseline and candidate runs.
Additional SELECT-only cases covered NULL, NaN, empty inputs and different bounds
on the same column. Failure counts, inclusive thresholds, extrema and bounded
failure samples matched. Scheduling and running services were unchanged.
This comparison loaded candidate modules in a disposable Python process, not
the service processes. The shared-range change was not deployed by this test.

## Repeat the contract checks

Run from the repository root after `make setup`:

```bash
uv run --locked pytest packages/phlo-dagster/tests/test_dagster_plugin.py
uv run --locked pytest packages/phlo-dagster/tests/test_runtime_image_contract.py
uv run --locked pytest packages/phlo-dbt/tests/test_transform.py
uv run --locked pytest packages/phlo-dbt/tests/test_asset_checks.py packages/phlo-dbt/tests/test_federation.py
uv run --locked pytest -m "not integration" packages/phlo-lineage/tests
uv run --locked pytest -m integration packages/phlo-dbt/tests/test_integration_dbt.py
uv run --locked pytest -m integration packages/phlo-dagster/tests/test_code_server_lifecycle.py
uv run --locked pytest packages/phlo-observe-plugin/tests tests/observability/test_observe_shim.py tests/observability/test_observe_pretty_drain.py
uv run --locked pytest packages/phlo-pandera/tests
uv run --locked pytest -m integration packages/phlo-pandera/tests/test_quality_decorator.py::test_aggregate_results_against_real_trino_nan_and_nulls
```

The dbt integration checks verify real output values, compatible reuse, changed
variables, models, profiles and WAP targets, explicit docs and artifact isolation.
The concurrent-build regression also covers one failed build.

The Dagster lifecycle check owns a bounded disposable PostgreSQL container.
`PHLO_TEST_POSTGRES_DSN`, if supplied, must name an empty disposable database;
the test deliberately terminates its idle sessions. It exercises sensor-triggered
execution, queued successful and failed jobs, code reload, code-server restart,
cron execution after connection loss and low idle connection counts. It uses real gRPC and
the default run launcher, not a mocked launch path. The storage integration CI
group runs both real-provider modules.

The capped local lifecycle check uses `DAGSTER_GRPC_TIMEOUT_SECONDS=180` for
slow subprocess imports. An earlier attempt at 0.2 CPU hit the default launch
timeout. Production RPC timeouts are unchanged. Full local type checking exceeded
its memory budget and was stopped; focused types passed, with the full check
left to remote CI.

A separate bounded Docker experiment verified that the code listener was
reachable on its private interface and refused connections on its shared data
interface. Generated Compose portability and identity are covered by parser and
render tests. These checks do not replace a complete generated-stack image
build, Windows runtime testing, release qualification or remote CI.
