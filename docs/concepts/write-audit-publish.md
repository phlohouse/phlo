# Write, audit, and publish

Phlo separates writing data from auditing it and publishing it because each stage answers a different operational question.

## Why writing is separate

An ingestion or transformation asset produces a table. Its job is to read source data, apply the declared schema, and write a consistent table version. The asset owns source-specific concerns such as pagination, replication mode, partition keys, and merge behaviour.

Separating the write stage gives the table store one clear writer contract. Downstream checks can inspect the written relation rather than reimplementing source access.

## Why auditing is separate

A quality check evaluates whether data meets a rule. Rules can cover nulls, uniqueness, ranges, accepted values, freshness, or a Pandera schema.

Checks can be blocking or advisory. A blocking check prevents the run from being treated as successful. An advisory check records an observation without turning every anomaly into a write failure.

This distinction makes failure policy explicit. A source outage, a schema conversion failure, and a warning about row-count movement do not have the same operational meaning.

## Why publishing is separate

Publishing identifies the stable data surface that consumers may use. It attaches ownership, audience, consumer, freshness, and SLA metadata to a relation.

A table can exist without being a published product. Publication is the point at which the project declares that a relation has a consumer-facing contract.

## How the stages connect

The usual flow is:

1. An asset writes a table.
2. Contract and quality checks audit the result.
3. A publish declaration describes the consumer surface.
4. Catalog and lineage metadata expose the relation.

The stages may be implemented by different plugins, but they share asset keys, relation metadata, and dependency declarations.

## How this affects failures

Write failures point to source, schema, partition, catalog, or storage issues. Audit failures point to data values or contract rules. Publication metadata failures point to ownership, consumer, or governance declarations.

The distinction helps operators choose the relevant logs and retry boundary. It also keeps a remediation from weakening a contract merely to make a write complete.

## Why a write is not publication

A successful write proves that a provider produced a table version. It does not prove that the table is ready for every declared consumer or that its ownership metadata is complete.

The table may still be missing an audit result, contain values outside an accepted range, or have freshness below its contract. Treating every written table as published would erase those distinctions.

Publication therefore remains an explicit declaration. A project can write intermediate tables for later transformations without exposing them as consumer-facing products.

## Why audits use the written relation

Audits run against the relation that consumers would read. This makes checks sensitive to serialisation, type conversion, partition selection, and merge behaviour.

An audit that runs only against an in-memory source frame can miss errors introduced while writing the table. Query-backed checks also allow a provider to evaluate large tables without loading every row into the process.

The check provider decides how to execute a rule, but the rule's target remains a logical table. This keeps neutral rules portable across supported query engines.

## How blocking policy is selected

A blocking check contributes to the run's success decision. An advisory check contributes evidence and telemetry while allowing the write or publication stage to continue.

The policy is useful when a project needs to distinguish a hard contract from a signal that should be investigated. Freshness warnings and row-count movement can be advisory, while key uniqueness or required-column failures can be blocking.

Provider-specific checks can add richer failure details without changing the distinction between a blocking and advisory result.

## How publication metadata is consumed

Governance commands can inspect publication declarations, ownership, consumers, access roles, and service-level expectations. Other integrations can use the same metadata to generate access plans or read models.

The metadata does not grant access by itself. Operators still configure identity providers, backend roles, secret storage, and ingress controls.

This separation gives projects a stable declaration while leaving deployment-specific enforcement to the services that own each boundary.

## What the stages share

The stages share asset keys, relation references, dependency metadata, and run context. They differ in the question each stage answers and the failure policy it applies.

This shared vocabulary lets operators trace a published table back to its write and audit evidence.

## Select a WAP publication environment

Branch-based WAP uses the selected environment's `nessie_ref` as both the base of its run-specific `pipeline-run-*` audit branch and the publication target. It does not implement cross-environment promotion.

Configure the target and the one supported WAP code location in `phlo.yaml`:

```yaml
default_environment: stage
environments:
  stage:
    dagster_location: warehouse-code
    nessie_ref: qa
  prod:
    dagster_location: production-code
    nessie_ref: main
wap:
  enabled: true
  repository_location_name: warehouse-code
```

From the project directory, use `phlo materialize <asset-name> --environment stage`, or supply `--environment stage` to `phlo backfill`. Omitting the option selects `default_environment`. An explicit selection authorises the configured target; backend availability never selects another environment. An unknown environment is an error, not a fallback to `main`. The selected ref must already exist.

### Supported routing boundary

The launcher, worker and sensors must use the same configured code location, project identity and process backend. Environment selection does not construct another provider. Cross-location routing, run or asset capability overrides, unknown catalog implementations and unverified writer routing are rejected. Nessie supports non-`main` targets with the process Iceberg and Trino providers when their configured endpoints match the catalog. Snapshot WAP remains restricted to `main`.

All three processes must share trusted, durable `.phlo/wap-reports` storage under `PHLO_PROJECT_PATH`. Do not grant untrusted callers write access to this directory. Immutable, content-addressed launch manifests bind the environment, target ref, code location, strategy and backend fingerprint. The fingerprint excludes credentials; credential rotation does not change it. Endpoint aliases are not interchangeable. Configuration changes can block an existing run, but cannot redirect its writes or publication. Changing only the configured default does not redirect an existing run either.

Before running an asset function, the worker validates the launch binding and the run's actual remote origin. It waits up to 30 seconds for the trusted launcher to register the physical run ID returned by Dagster. Copied tags cannot register another run. A missing or lost launch response blocks execution and retains the branch; there is no first-claim-wins recovery. Authenticated remote submission requirements still apply.

Publication pins the source hash when promotion begins, proves that it contains the captured target ancestor, then fast-forwards the target with Nessie's conditional reference assignment. The PUT body contains that pinned source hash and `expectedHash` contains the launch target hash. Ordinary Nessie merge permits conflict-free rebasing even with a target hash in its URL, so it is not a strict publication precondition. Missing or incomplete ancestry proof blocks publication. The report records `publication_method: ref_fast_forward`, not a newly created merge commit.

By default, promotion does not bind check results to the hash they inspected. Keep the run-specific audit branch under one writer's control and do not change it after checks finish. Otherwise, a change before promotion pins its source hash can publish using earlier passing checks. Enable candidate-bound checks below to reject that stale evidence.

Target movement blocks publication instead of rebasing the audit onto new data. A temporary catalog read failure is recorded as `catalog_hash_unavailable`; target movement is recorded separately as `target_hash_drift`. Foreign-location runs cannot trigger publication or retention deletion. Acknowledged publication recovery replays evidence and cleanup without publishing again or replacing the recorded publication hash with a later target head. Cleanup is also conditional on the audited source hash, so a changed or recreated source branch is retained for review.

### Require candidate-bound checks

For branch WAP, opt in through `phlo.yaml`:

```yaml
wap:
  enabled: true
  require_candidate_bound_checks: true
```

Phlo's standalone callable checks capture the candidate branch and hash before execution and verify that the hash is unchanged afterwards. Their Dagster results include `phlo/wap_candidate` metadata. Promotion requires every executed check to identify the same candidate hash it will publish. Advisory checks retain their warning semantics, but must still identify their candidate. Runs without checks cannot publish in this mode.

Custom Dagster checks and in-band checks returned by asset functions must attach their own binding. Import `bind_wap_audit` from `phlo_dagster.wap_launch`, enter `with bind_wap_audit(context.run) as metadata` around the read-only check, and include that metadata in its result. Query the run's `phlo/ref` inside the scope. In-band checks are not automatically bound around the asset function because that function also writes data. Do not manufacture a binding after evaluating a check.

The helper checks branch stability; it does not freeze the branch, inspect arbitrary SQL, or prove the identity of comparison data outside the candidate. Checks must use the candidate ref, and the branch must remain under trusted writer control without concurrent resets. This mode conservatively rejects even unrelated candidate-branch commits after an audit.

A missing binding blocks publication with `audit_candidate_binding_missing`. A different branch or hash blocks it with `audit_candidate_changed`. Both retain the branch and leave the publication target unchanged, without recording promotion intent. A change during the helper's scope fails the check execution. Re-audit through a new WAP run after a candidate changes; do not edit old check events or reports.

The policy is part of the immutable launch context. Changing it blocks existing runs through configuration-drift validation; it cannot downgrade a run already launched in strict mode. Deploy launchers, workers and sensors together before enabling it. Snapshot WAP rejects this option because this binding covers Nessie branch identity, not Iceberg snapshot-promotion identity. Leaving the option disabled preserves existing custom-check and no-check behaviour.

### Review blocked or legacy runs

Existing unbound `main` runs are **not** automatically upgraded or published. They remain blocked with `legacy_launch_context_missing`, and their branches and data are retained rather than expired. Newly launched, bound runs targeting `main` remain supported.

1. Preserve the run report, immutable manifests, Dagster run origin and branch history before investigating. Do not edit tags or manifests to bypass the gate.
2. For a missing physical registration, verify the authenticated launch response and the exact physical run's project, origin and tags. If that identity cannot be independently established, leave the branch retained and launch a new run. There is no automatic registration-repair command.
3. For configuration drift, restore the reviewed original configuration if appropriate, then retry the same registered run. Otherwise launch a new run against the intended configured target. Do not retarget an old report.
4. For `target_hash_drift`, audit a new run based on the current target. For `merge_outcome_unknown`, inspect authoritative catalog commit history and obtain an operator-reviewed merge receipt before any manual recovery; target movement alone is not proof that this run merged.
5. For legacy runs, independently review their data and history, then relaunch with a new bound audit branch. Any manual publication or deletion requires a separately authorised operator procedure. Keep the old branch until that review and the applicable retention decision are complete.

Deploy the launcher, workers and sensors together. Restarting a registered worker is safe because registration is durable. A WAP backfill resume also checks its saved environment context; legacy backfill state without routing proof requires review and a new backfill rather than automatic migration.
