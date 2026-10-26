# Dagger core pilot

This optional experiment compares Dagger 1.0.0-beta.21 with Phlo's existing
non-integration core-test shards. It does not replace required CI, coverage
floors, core-regression tests, integration acceptance, or release evidence.

## Run the pilot

Use a Linux host with Docker, Python 3.12, and the repository's development
dependencies. Run commands from the repository root.

1. Run `make setup`.
2. Install [Dagger 1.0.0-beta.21](https://docs.dagger.io/getting-started/install).
   The pilot workflow downloads the versioned Linux amd64 archive and verifies
   its SHA-256 before extraction. No cloud account is required.
3. Run the native comparison:

   ```bash
   make test-core-shard CORE_TEST_SHARD_INDEX=0
   ```

4. Run Dagger twice:

   ```bash
   python3 scripts/dagger_core.py --shard 0 --output test-results/dagger-cold
   python3 scripts/dagger_core.py --shard 0 --output test-results/dagger-warm
   ```

5. Repeat for shards 1 and 2. Each shard uses two pytest workers. The command,
   marker exclusions, shard algorithm, and coverage arguments are shared with
   required CI through `make test-core-shard`.

Dagger prints end-to-end elapsed time and exports JUnit and coverage XML under
the selected output directory, in `shard-<index>`. A failed test returns a
non-zero status and retains its reports. The CLI trace identifies cached
operations. Repeated output can be replayed from cache; it is not proof that
pytest executed again.

Only Git-tracked working-tree files enter the container. Stage new files before
testing them, using `git add -N` if you do not want to stage their contents.
Deleted files are omitted. Host environment variables, ignored files, Docker
sockets, and the host `.git` directory are not passed to tests. The container
creates a credential-free snapshot commit for tests that inspect tracked files.
This does not reproduce repository history or the original commit identity.

The dependency operation imports only `uv.lock` and workspace manifests, then
runs `uv sync --dev --locked --no-install-workspace`. A later operation adds
the full tracked snapshot and installs workspace packages. Ordinary source
edits must rerun tests but should reuse external dependency installation.
The pilot deliberately imports all tracked source for test execution because
core tests inspect workflows, templates, examples, and package data.

## Compare results before timings

The non-blocking **Dagger core pilot** workflow runs the native shard, a cold
Dagger run, and an identical cached run. It requires matching JUnit test
identities and pass, failure, and skip outcomes. Its artifacts retain timing
files, traces, and reports even on failure. The native timing excludes the
preceding workspace setup. The cold Dagger timing includes engine startup,
image retrieval, system packages, and dependency installation. Do not describe
those timings as equivalent cold bootstraps.

For an adoption decision, collect at least five serial samples per variant on
equivalent resources. Alternate native and Dagger runs. Record medians and
ranges, and distinguish an actual test execution from a cached result.
The workflow's single cold and warm samples are diagnostics, not a performance
verdict. Client peak RSS excludes the Dagger engine and test containers.

Exercise these invalidation cases in a disposable checkout:

| Change | Required observation |
| --- | --- |
| No change | Test execution is cached; exported outcomes match the native run. |
| Core implementation or test edit | Tests execute again; external dependency installation stays cached. |
| Shared workspace package edit | Tests execute again with the changed package. |
| Lockfile or manifest edit | Dependency installation is reconsidered; an inconsistent lock fails. |
| Deliberate failing assertion | Both commands fail for that assertion and retain its JUnit failure. |

Restore each disposable edit before proceeding. Do not retry a failed test
until it passes and count that as acceptance. Record flaky or environment-
dependent outcomes separately and investigate them.

For concrete probes, append a comment to `src/phlo/security/mode.py`, then to
`packages/phlo-testing/src/phlo_testing/__init__.py`, restoring each before the
next run. Append a comment to `uv.lock` to check dependency invalidation.
In `tests/runtime/test_native_process_manager.py`, change the first
`assert mgr.can_run_dev(svc) is True` to assert `False` with the message
`dagger deliberate failure`. Run shard 0 natively and with Dagger before
restoring the assertion. To check lock enforcement, temporarily replace
`"pytest>=9.0.3"` with `"pytest==0.0.0"` in `pyproject.toml` and require a
non-zero Dagger exit before pytest starts.

## Adoption criteria

Correct invalidation and matching test outcomes are mandatory. Reused test
success cannot substitute for fresh scans or repeated release qualification.
Adopt beyond this pilot only if edited-code feedback improves by at least 25%
and 30 seconds, cold execution is within 20% of an equivalent native bootstrap
or its cost is demonstrably offset, and failures remain reproducible.

Keep the existing test commands authoritative. Do not expand the pilot if
maintaining it requires duplicate package inventories, dependency definitions,
or test exclusions. Shared-engine performance, cross-orb cache reuse, engine
resource usage, native Windows behaviour, service-backed tests, and Dagger's
official pytest-module tracing are outside this experiment.

## Initial local findings

The October 2026 Linux-orb experiment matched native and Dagger JUnit outcomes
across all three shards: 2,839 passed and three skipped. The comparison also
rejected a changed test identity and a missing report. Both runners retained
the same deliberate assertion failure and returned non-zero statuses.
A lockfile comment invalidated dependency installation, and the inconsistent
manifest probe failed before pytest started.

These are diagnostic samples, not an adoption benchmark. Native Python was
3.12.13, container Python was 3.12.15, and some runs overlapped broader checks.
With dependency installation cached, the corrected shard 1 took 171.5 seconds
in Dagger versus 90.8 seconds natively. An unchanged repeat took 0.8 seconds,
but replayed cached tests. A source edit reran shard 0 in 121.7 seconds while
retaining the dependency cache. An isolated shared-package edit did the same
in 127.5 seconds. No edited-code speedup has been established.
GitHub-hosted runners also need an explicit cache strategy for reuse between
jobs or pull requests. This pilot measures reuse within one engine only.

Development runs exposed a nested-make defect in the pilot's contract test
and dependency-cache misses after source edits. Both were corrected and
rechecked. An earlier full check hit Loki regex timeouts, and an exploratory
Dagger run hit a process-liveness race. Those unrelated paths were not changed.
Another shared-package probe lost two pytest workers to the 60-second
faulthandler timeout during a documentation build. That build exited 137.
The fresh, isolated probe passed without changing test timeouts. This does
not establish the timeout's cause. The focused Loki tests and the final
`make check` passed. Retain the earlier failures as reliability observations,
not successful acceptance samples.
