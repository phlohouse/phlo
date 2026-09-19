# Write, audit, and publish

Phlo separates writing data from auditing it and publishing it because each stage answers a different operational question.

## Why writing is separate

An ingestion or transformation asset produces a table. Its job is to read source data, apply the declared schema, and write a consistent table version. The asset owns source-specific concerns such as pagination, replication mode, partition keys, and merge behavior.

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

Audits run against the relation that consumers would read. This makes checks sensitive to serialization, type conversion, partition selection, and merge behavior.

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
