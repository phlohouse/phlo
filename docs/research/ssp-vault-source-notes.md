# Useful engineering practices from the SSP vault

Research date: 26 October 2026.

These source notes retain the initial research into recovery and consumer safety. Their example-led suggestions are verification ideas, not the requested product roadmap. The [core-package assessment](ssp-vault-phlo-assessment.md) supersedes their implementation priorities and identifies changes to Flow Core and provider packages first. Examples demonstrate those changes afterwards.

These are source-led recommendations for Phlo maintainers, not findings that Phlo lacks the capabilities. The accompanying repository investigation owns the capability mapping. In particular, the supplied mapping already identifies immutable history replay, Iceberg schema policies, Nessie write-audit-publish, maintenance planning and automation, governance metadata, attempt-scoped reports, reconciliation, and watermark helpers. Reuse those implementations and existing examples rather than add equivalent frameworks.

## Scope and source quality

I read the [Data Engineering Vault index](https://www.ssp.sh/brain/data-engineering/) and the bodies of the ten engineering notes listed below. I also inspected relevant excerpts of the [concept map](https://www.ssp.sh/brain/data-engineering-concepts/). This is a purposive sample covering the requested subjects, not an exhaustive review of the site's advertised 1,000-plus interconnected notes. I did not crawl the graph, watch linked videos, audit commercial offerings, or benchmark tools.

| Vault note read | Useful contribution |
| --- | --- |
| [Functional data engineering](https://www.ssp.sh/brain/functional-data-engineering/) | Explicit inputs, versioned logic, reproducibility, and partition-scoped replacement. |
| [Idempotency](https://www.ssp.sh/brain/idempotency/) | Retry safety and the danger of unbounded date filters and mutable source inputs. |
| [Load incremental and idempotency](https://www.ssp.sh/brain/load-incremental-and-idempotency/) | Smaller units of work and repeatable historical analysis. Some claims need qualification. |
| [Data contracts](https://www.ssp.sh/brain/data-contracts/) | Asset-level assertions and the producer-consumer agreement, including enforcement and ownership. |
| [Schema evolution](https://www.ssp.sh/brain/schema-evolution/) | Compatibility and change management beyond accepting a new column. |
| [Software-defined asset](https://www.ssp.sh/brain/software-defined-asset/) | Version-controlled definitions and dependencies on physical data assets. |
| [Dagster](https://www.ssp.sh/brain/dagster/) | Local testing, partitioned execution, operational visibility, and reusable asset definitions. |
| [dbt](https://www.ssp.sh/brain/dbt/) | Reviewable SQL dependencies and tests, with a warning against unnecessary model proliferation. |
| [Apache Iceberg](https://www.ssp.sh/brain/apache-iceberg/) | Snapshots, schema evolution, and file compaction. |
| [Data integration CLI tools](https://www.ssp.sh/brain/data-integration-cli-tools/) | Keeping integration usable as code rather than requiring another platform. Tool rankings are opinion, not evidence. |

Several pages label themselves growing or unfinished. Their footer also advertises a different note count from the index. Neither count is independently verified here. Guessed standalone quality and observability note URLs returned 404, so the quality recommendations derive from the contract, Dagster, and dbt notes and their official documentation instead.

The technical evidence below comes from official documentation read during this investigation. Iceberg's `latest` pages identified themselves as Java documentation for 1.12.0, and the dlt contract page as 1.30.0. Those versions do not establish Phlo compatibility. Dagster and dbt documentation is rolling documentation. Check the versions and actual engines used by Phlo before adopting any API or configuration detail. All summaries are original.

## 1. Prove retries after a committed write

The vault's [functional pipeline note](https://www.ssp.sh/brain/functional-data-engineering/) and [idempotency note](https://www.ssp.sh/brain/idempotency/) make reruns a design property. The transferable idea is an observable recovery contract, not a blanket requirement to overwrite every table.

Primary evidence:

- [Dagster backfills](https://docs.dagster.io/guides/build/partitions-and-backfills/backfilling-data) demonstrates reading an explicit time window, computing a result, and overwriting that same window. The orchestrator supplies the window. The application still implements the write semantics.
- [dlt merge loading](https://dlthub.com/docs/general-usage/merge-loading) documents materially different strategies. The default delete-insert strategy deduplicates staging data when a primary key is supplied. Upsert expects unique primary keys rather than deduplicating them. Merge without primary or merge keys falls back to append.

Extend an existing Phlo example, preferably the WAP Failure Lab, with a failure after the destination commits but before the attempt reports success. Retry the same logical input, then query the published result. Check keys, values, row counts, and publication state, rather than equating a green run with correctness. Include a control with a genuinely new batch so that a retry fix cannot silently suppress new data. Capture the source identity, logic version, destination snapshot or branch identity, and attempt reports where the existing interfaces expose them.

The supplied repository mapping says the lab's append retry currently happens before `yield`. That demonstration does not prove committed-write replay safety. This recommendation extends the demonstration, not Phlo's replay framework.

Tradeoff: a realistic failure needs integration infrastructure and a reliable fault-injection point. Different write strategies need different expected outcomes. Logical idempotency does not require identical snapshot IDs or the absence of all orphan files.

Avoid assuming that `MERGE`, a retry decorator, an asset definition, or a WAP branch provides exactly-once processing. The keys, conflict handling, commit boundary, and publication boundary decide the result.

## 2. Make historical ranges and late-arrival behaviour visible

The [incremental loading note](https://www.ssp.sh/brain/load-incremental-and-idempotency/) is useful as a prompt to reduce work per run. Its implication that incremental loading means moving from batch to event-driven processing is too broad. Scheduled incremental batches remain batches.

Primary evidence:

- [dlt cursor loading](https://dlthub.com/docs/general-usage/incremental/cursor) documents inclusive starts and exclusive ends by default. Providing `end_value` makes a backfill stateless with respect to the existing incremental cursor.
- The same page explicitly distinguishes boundary deduplication from destination-wide merge deduplication. Boundary deduplication is disabled when `end_value` is supplied or lag is used.
- [Dagster backfill policies](https://docs.dagster.io/guides/build/partitions-and-backfills/backfilling-data) supports multiple partitions in one run when the asset handles a range, so one task instance per partition is not a universal requirement.

Use the existing watermark helpers and immutable history replay in a small example with adjacent ranges, a boundary record, and a late update. Explain which timestamp selects records, which state advances after success, and whether late data is merged, replayed, or rejected. Demonstrate that a historical replay does not unexpectedly move the live ingestion cursor. When a source cannot reproduce old inputs, state that limitation rather than promise historical reproducibility.

Tradeoff: lookback windows increase source reads and require destination conflict handling. Full historical snapshots can be expensive. Retain enough source evidence for the agreed recovery period, subject to privacy and deletion requirements.

Avoid adopting permanent raw retention, full dimension snapshots, processing-time partitioning, or a ban on historical dependencies as universal rules from the functional note. Choose them for a workload, not as prerequisites for all Phlo pipelines.

## 3. Test consumer compatibility separately from schema acceptance

The [contracts note](https://www.ssp.sh/brain/data-contracts/) usefully connects schema, semantics, ownership, and enforcement. The [schema evolution note](https://www.ssp.sh/brain/schema-evolution/) motivates compatibility review, but storage-level evolution is not a guarantee that consumer queries still work.

Primary evidence:

- [Iceberg evolution](https://iceberg.apache.org/docs/latest/evolution/) uses column IDs to preserve field identity across supported schema changes. Add, drop, rename, supported widening, and reorder are metadata changes that do not rewrite data files. A SQL consumer referencing an old name still needs migration.
- [dlt schema contracts](https://dlthub.com/docs/general-usage/schema-contracts) distinguishes evolve, freeze, discard-row, and discard-value modes. Enforcement has important boundaries, including initial schema inference for new tables and different handling of nested data with Pydantic validation.
- [dbt model contracts](https://docs.getdbt.com/docs/mesh/govern/model-contracts) checks the declared output shape. It requires every column's name and type. Constraint enforcement depends on the platform, and data tests validate content after a model is built.

Extend the existing SaaS API schema-evolution example with a downstream query and a business assertion. Exercise a nullable addition, a rename, a type change, and a meaning change with no type change. Use the existing strict, additive, and drop-extra policies. Show the consumer-visible outcome under each relevant policy and require an explicit migration decision for a breaking change. Keep the contract near the asset's existing governance metadata, including its owner and units where relevant.

Tradeoff: strict policies stop useful ingestion when producers change unexpectedly. Permissive policies preserve availability but can conceal lost fields or incorrect meanings. A schema accepted by ingestion can still violate a consumer's assumptions.

Avoid a second contract registry or mandatory Protobuf or Avro layer without a concrete cross-team need. Do not treat dropping unexpected fields as harmless, or a schema-only contract as proof of business correctness.

## 4. Demonstrate that failed checks prevent publication

The [contracts note](https://www.ssp.sh/brain/data-contracts/) favours assertions on assets. Its suggestion that each update makes the contract certain is stronger than the evidence supports. Assertions protect only the conditions tested and only where enforcement occurs.

Primary evidence:

- [Dagster asset checks](https://docs.dagster.io/guides/test/asset-checks) says failed checks do not block downstream materialisation by default. A blocking check can prevent downstream materialisation. The example writes the checked asset before the check reads it, so blocking is not equivalent to hiding that asset from external readers.
- [dbt contracts versus tests](https://docs.getdbt.com/docs/mesh/govern/model-contracts) distinguishes shape guarantees at build time from flexible content checks after the model exists.

Use the existing Nessie WAP flow and reconciliation checks to demonstrate a candidate that passes structural validation but fails a business or reconciliation check. Query the published branch before and after the failed attempt and show that consumers still see the last approved data. Then correct the candidate, publish it, and show the intended change. Include advisory and blocking outcomes in the existing run reports, with an owner and recovery action.

Tradeoff: publication gates delay data when checks fail. Advisory checks reduce interruption but permit known risks. Checks also consume query resources, so start with business-critical assertions rather than exhaustive scans on every asset.

Avoid relying on an orchestrator's failed task status to protect direct SQL readers. Do not rebuild WAP or a quality framework. Prove the existing publication boundary and its failure handling.

## 5. Distinguish execution health from data usefulness

The [software-defined asset note](https://www.ssp.sh/brain/software-defined-asset/) shifts attention from tasks to persisted outputs. Combined with the [Dagster note](https://www.ssp.sh/brain/dagster/), this suggests making asset evidence useful for recovery rather than collecting more logs.

Primary evidence:

- [dbt freshness configuration](https://docs.getdbt.com/reference/resource-properties/freshness) defines separate warning and error thresholds and supports timestamp-field or adapter-dependent metadata calculations. Without either threshold, dbt does not calculate freshness for that source.
- [dbt source freshness jobs](https://docs.getdbt.com/docs/deploy/source-freshness) documents different failure behaviour for the platform checkbox and an explicit command step. Detection and enforcement are separate choices.
- [Dagster check results](https://docs.dagster.io/guides/test/asset-checks) can carry metadata and severity, making the checked condition inspectable rather than just recording an execution status.

Extend the existing source-to-report example and durable attempt-scoped reports with a successful run containing stale or incomplete source data. Present execution status, freshness, completeness, and publication decision separately. Prefer a compact set of asset-specific evidence: input interval or watermark, row-count reconciliation, source and destination identity, check outcome, and published version. Define freshness against the timestamp that reflects the consumer's need, not automatically the newest ingestion timestamp.

Tradeoff: a recently loaded record does not prove that all expected records arrived. Freshness and completeness need different checks. Avoid high-cardinality metric labels and exposing sensitive failing records in logs or reports.

Avoid a second observability service or a universal anomaly detector as the first step. Use the existing report API and Observatory to answer why a consumer should trust this output and what to do when they cannot.

## 6. Connect maintenance policy to the recovery promise

The [Iceberg note](https://www.ssp.sh/brain/apache-iceberg/) makes snapshots and compaction attractive. The operational lesson is that retained history, file layout, and deletion policy require separate decisions.

Primary evidence comes from [Iceberg maintenance](https://iceberg.apache.org/docs/latest/maintenance/):

- Expiring snapshots removes those versions from time travel and allows removal of files no longer needed by retained snapshots.
- Orphan deletion with a retention interval shorter than a write's duration can delete in-progress files and corrupt a table. The documented default is three days, not a universal safe value.
- Path representation mismatches can also cause unsafe orphan deletion.
- Small files increase metadata and file-open overhead. Compaction is optional, and the `files` metadata table helps decide where it is useful.

Extend the existing plan/apply maintenance workflow and automation sensor with a disposable-table acceptance scenario. Show retained replay inputs and snapshots, inspect a plan, apply the approved operations, and verify logical row equality afterwards. Define the rollback period and maximum writer duration before setting expiration or orphan cleanup. Include branch and tag retention behaviour in the engine-specific review before enabling deletion.

Tradeoff: longer retention costs storage, but shorter retention reduces recovery options. Compaction consumes compute and creates commits. Source replay and table time travel are different recovery paths and can have different retention requirements.

Avoid a fixed daily compaction rule for every table, blind object-store lifecycle deletion, or copying a documentation retention example into production. The Java and Spark documentation does not prove that the same operation exists in Phlo's selected Python or SQL engine.

## 7. Keep one example usable at several levels of realism

The [CLI tools note](https://www.ssp.sh/brain/data-integration-cli-tools/) favours library-shaped integration, while the [Dagster](https://www.ssp.sh/brain/dagster/) and [dbt](https://www.ssp.sh/brain/dbt/) notes favour version-controlled computation. Adopt the short development loop, not another catalogue of connectors.

Primary evidence from [Dagster unit testing](https://docs.dagster.io/guides/test/unit-testing-assets-and-ops) shows direct invocation with explicit inputs and mocked resources. It also demonstrates in-process execution with an IO manager for assets that need orchestration context. These tests verify computation without proving remote storage or commit behaviour.

Keep the transformation and fixture data shared across a fast local test, a real local Iceberg test, and the existing source-to-report or WAP example. Document what each level proves. Make the committed-write retry case from recommendation 1 the integration acceptance check rather than hiding it behind many unit tests. Prefer existing CLI commands and provider contracts, and keep credentials outside fixtures and committed configuration.

Tradeoff: mocks are fast but miss engine behaviour. Full-stack examples catch commit and publication errors but cost more to run. Both are useful when the boundary is explicit.

Avoid duplicating business logic in a special demo implementation, adding a new configuration language, requiring a production-sized deployment for every edit, or treating an in-process materialisation as proof of distributed failure recovery.

## Suggested order and remaining verification

Start with recommendation 1 because the supplied local mapping identifies a precise unproven failure boundary. Add recommendations 2 through 5 as cases in the existing examples. Apply recommendation 6 only with explicit retention and engine-specific deletion checks. Use recommendation 7 to keep those examples practical to maintain.

This investigation verifies external documentation, not Phlo runtime behaviour. It does not establish which proposed cases already have test coverage, validate provider versions, run a failure lab, or authorise a production change. The next step is to compare these evidence requirements with the independently collected Phlo capability map and keep only the missing demonstrations or explanations.
