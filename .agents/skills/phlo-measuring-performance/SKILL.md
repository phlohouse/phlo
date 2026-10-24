---
name: phlo-measuring-performance
description: "Measures Phlo work counts, latency, and peak memory against a reproducible baseline. Use for slow CLI, discovery, ingestion, quality checks, queries, or Observatory flows, and proposed caches."
---

# Measure performance

Define the workload and acceptable trade-offs before optimising. Use the
[quality performance record](../../../docs/contributing/quality-performance.md)
as an example of reporting both gains and limitations, not as a universal budget.

## 1. Fix the measurement contract

Record the affected operation, dataset or fixture, output oracle, baseline and
candidate revisions, environment, and acceptance rule. Separate cold startup
from warm operation. Identify a competing implementation that produces wrong
results, and choose data that distinguishes it from the correct output.

Count work before interpreting elapsed time: registry discovery calls, file
reads, SQL statements, rows materialised in Python, subprocesses, API requests,
or browser renders. Record wall time and peak memory separately. Finish when
the expected output and the metric that would reject the change are explicit.

## 2. Reuse a real workload

Inspect the existing benchmark that matches the operation:

- `scripts/benchmarks/quality_algorithms.py` compares quality validation,
  aggregates, grouped reconciliation, and merge-key preparation across checkouts.
- `scripts/benchmarks/discovery_registry_microbench.py` measures its declared
  discovery and registry microbenchmarks, not complete CLI or service startup.

Run each script's `--help` before selecting size and repeats. For quality work,
follow the reproduction commands in the performance record. Keep the baseline
checkout pristine and verify imported module paths so both variants cannot
accidentally measure the same editable installation.

For other operations, extend an existing workload when possible. Use real
transport and provider work when claiming end-to-end latency. Label mocked,
no-I/O, and component measurements with their actual scope. Use
[Observatory testing](../phlo-testing-observatory/SKILL.md) to establish the
browser journey before profiling it.

## 3. Remove measured unnecessary work

Locate the owner of repeated work. Prefer removing it, calculating it once at
that owner, or batching independent operations before adding a cache. Preserve
authorization, transaction, ordering, freshness, and failure contracts.

If caching is necessary, identify the complete key, invalidating writes, size
bound, and lifetime. Exercise a changed input that must miss or invalidate the
cache. A fast repeated request alone does not prove correctness.

## 4. Compare equivalent runs

Run baseline and candidate serially with identical inputs and resource limits.
Alternate their order where the harness permits. Start with at least five
samples per variant for timing claims; report the actual count and spread.
If a costly workload needs fewer samples, state the limitation rather than
presenting a precise tail-latency claim. Separate fixture generation and imports
from measured work where the harness does so.

Check independent output assertions on every run. Report operation counts,
median wall time, and peak RSS with the measurement method. Report p95 only
with sufficient samples to support it. Distinguish process-wide peak RSS from
an allocation delta, and algorithm-only work from provider I/O.

## 5. Decide and retain evidence

Compare every measured metric with the acceptance rule. A speed gain with higher
memory is a trade-off, not an unconditional improvement. Rework changes without
a reproducible benefit, or request explicit acceptance of the demonstrated
trade-off. Run the owning correctness tests and selected broader checks from the
[verification matrix](../../../docs/contributing/verification-matrix.md).

Report rerunnable commands, revisions, workload sizes, raw-result location,
before-and-after measurements, and unmeasured boundaries. Keep permanent tests
for observable regression risks rather than noisy host-specific timing claims.
