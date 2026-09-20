# Glossary

## Core Terms

- `capability`: a stable abstract contract implemented by one or more packages
- `provider`: a package component that fulfills a capability or plugin role
- `service package`: a package that contributes runtime services and related config
- `surface`: an external entry point such as an API, UI, or metadata system
- `publish target`: the destination exposed after validation and promotion
- `WAP`: Write-Audit-Publish, the branch-first publish model used in the platform story
- `marts`: serving-oriented modeled tables intended for downstream consumption
- `catalog`: the system that tracks tables, branches, or metadata views of the data plane
- `resource provider`: package code that exposes runtime resources to orchestrators or workflows
- `asset provider`: package code that exposes asset definitions into orchestration

## Why This Exists

Phlo reuses terms across packages. This page keeps the docs vocabulary consistent.

## Asset

A named unit of data work discovered by an orchestrator or provider.

## Catalog

A metadata service that resolves table identifiers, schemas, snapshots, and branches.

## Consumer

A declaration describing a dataset consumer.

## Contract

Governance metadata that describes ownership, consumers, service expectations, privacy, or lifecycle.

## Dagster

The orchestrator in the default stack. Every asset runs as a Dagster run, whether launched by `phlo materialize`, `phlo backfill`, a schedule, or the Dagster UI at `http://localhost:10006`.

## DLT

A data-loading library used by `phlo-dlt` ingestion assets.

## Iceberg

The table format used by the default lakehouse storage path.

## Nessie

The versioned catalog used by the default Iceberg stack.

## Partition

A logical slice of an asset identified by a partition key such as a date.

## Plugin

An installed extension discovered through a Python entry point.

## Quality check

A validation that returns a structured result with metrics and metadata.

## Reference

A catalog branch name used to resolve versioned table metadata.

## Service

A local or deployed infrastructure component described by a package service manifest.

## Tenant

The logical authorisation boundary associated with principals and resources.

## Trino

The SQL query engine used to query Iceberg tables in the generated stack.

## Dataset

A canonical projection of governed table identity, workflow state, and publication state.

## Evidence pack

A tamper-evident ZIP archive containing audit records, signatures, and system manifest data.

## Run evidence

Structured records describing a pipeline run, its events, stages, resources, lineage, quality results, and artefacts.

## Hook

An extension handler registered on the Phlo hook bus for selected event types, assets, or tags.

## Sensor

A Dagster definition that polls state and can launch or coordinate work such as WAP promotion, maintenance, or failure alerts.

## Selector

A Dagster asset selection expression passed to `phlo materialize --select`.

## Regulated mode

The security mode enabled by `PHLO_REGULATED` that applies regulated validation and enforcement paths.

## Native mode

Development mode in which services with a `dev` command are started as host subprocesses.

## Branch

A versioned catalog reference used to isolate, compare, merge, or publish table changes.
