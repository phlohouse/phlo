# How Phlo works

Phlo turns workflow code into discoverable data assets and governance metadata. This page explains how a project moves from source files to a materialised table.

## What a Phlo project contains

A project has a `phlo.yaml` file, workflow modules, schemas, tests, and data contracts. The default template places workflow code under `workflows/`.

Workflow modules declare assets with provider decorators. A DLT decorator registers an ingestion asset. A quality decorator registers checks. Flow decorators register provider-neutral metadata.

## How discovery works

The CLI loads the configured workflow path and imports Python modules. Installed plugins contribute asset providers, query engines, catalogs, and service definitions through Python entry points.

Discovery creates `AssetSpec` and related capability specifications. These specifications describe names, groups, dependencies, checks, and run behaviour. The core package does not require one orchestrator for every declaration.

## How execution works

Provider adapters translate specifications into runtime definitions. The default stack uses Dagster for orchestration, DLT for ingestion, Iceberg for tables, Nessie for catalog metadata, MinIO for object storage, and Trino for queries.

The adapter invokes the source function, writes data, evaluates checks, and records the result in the provider's runtime system. A failed blocking check prevents a successful publication.

## How data moves

An ingestion asset reads from a source and writes a table in the configured namespace. A transformation reads existing relations and writes a derived table. A published asset identifies a table intended for consumers.

Relations can be resolved with `ref` and `source`. These return a `LogicalRelation` that renders a known physical relation when discovery has resolved one.

## Where configuration applies

`phlo.yaml` describes infrastructure and service overrides. Settings classes read package environment variables. The CLI uses the project root to resolve `.phlo/.env` and `.phlo/.env.local`.

Capability selection can come from project defaults, workflow tags, or asset-level overrides. A provider is required only when a declaration or adapter needs that capability.

## What remains provider-specific

The asset model is shared, but execution details remain with providers. DLT controls source and load behaviour. Dagster controls orchestration. Iceberg and Nessie control table and catalog operations.

This separation lets a project describe data once while choosing compatible runtime components for local development or deployment.

## How discovery preserves intent

Discovery reads declarations before execution begins. It records the declared table, group, schedule, dependencies, quality checks, ownership, and capability choices without running the asset function.

The resulting specification is the boundary between project code and runtime adapters. It gives an adapter enough information to build an executable definition while preserving the project author's intent.

Discovery also makes validation earlier. Missing packages, invalid configuration, duplicate asset keys, and unsupported provider choices can be reported before a data source is contacted.

## How dependencies become a graph

Dependencies are expressed with asset keys and relation references. The registry resolves those keys and builds a graph that an orchestrator can schedule.

A dependency can point to an asset in the same project or to a source relation provided by a capability. The reference retains logical names until the selected catalog and query engine resolve physical details.

The graph provides ordering without requiring every workflow function to call another function directly. This keeps orchestration concerns out of transformation and ingestion code.

## How metadata follows a table

An asset key identifies a logical product across discovery, execution, catalog operations, checks, and publication. Run metadata can attach source details, partition values, quality results, and ownership to that key.

Catalog metadata records the table location and table version. Governance metadata records the declared owner, audience, consumers, PII status, lifecycle, and service-level expectations.

The shared key is what lets the CLI, orchestrator, catalog, and governance layers refer to the same data product without duplicating provider-specific names.

## How local and deployed runs differ

Local runs use the same declarations with a local service stack and project environment. The configured backend determines how the CLI starts services, resolves credentials, and invokes providers.

Deployed runs can use managed services or different provider packages. The asset and metadata model remains stable while service discovery, credentials, network addresses, and execution placement change.

This boundary keeps development commands useful without making local container names part of the workflow contract.

## What remains provider-specific

Source credentials, retry behaviour, table commits, query syntax, service health, and orchestration retries remain provider concerns. Phlo carries their outcomes as shared metadata and errors.

That boundary allows a project to change one provider without rewriting the declarations that describe its data products.
