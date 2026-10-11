# phlo-polaris

Apache Polaris catalog service plugin for Phlo — an Iceberg REST catalog with
OAuth/RBAC and credential vending, plus snapshot-based Write-Audit-Publish.

## Description

`phlo-polaris` is a **catalog alternative** to `phlo-nessie`, not a Nessie
emulation. It provides the pinned Apache Polaris service with a PostgreSQL
metastore and MinIO/RustFS-compatible S3 storage, and implements Phlo's
snapshot promotion contract: runs stage immutable candidate Iceberg snapshots,
quality checks audit those exact snapshots, and a durable release pointer
(compare-and-swap guarded) exposes them only after promotion.

A project selects one catalog per warehouse: `catalog: nessie` (branch/merge
WAP) or `catalog: polaris` (snapshot WAP). Both may not be the default writer
for the same warehouse. Nessie remains fully supported; use
`phlo polaris migrate-from-nessie` (dry-run by default) to move metadata.

## Installation

```bash
pip install phlo-polaris
# or
phlo plugin install polaris
```

## Configuration

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `POLARIS_PORT` | `10018` | Polaris API host port |
| `POLARIS_ROOT_CREDENTIALS` | auto-generated | Bootstrap principal (`client_id:client_secret`), secret |
| `POLARIS_WRITER_CLIENT_ID` | `phlo_writer` | Writer principal client id |
| `POLARIS_WRITER_CLIENT_SECRET` | auto-generated | Writer principal secret, secret |
| `POLARIS_READER_CLIENT_ID` | `phlo_reader` | Reader principal client id |
| `POLARIS_READER_CLIENT_SECRET` | auto-generated | Reader principal secret, secret |

`phlo services init` preserves existing project secrets and generates missing
ones. The one-shot Polaris admin bootstrap converts `POLARIS_ROOT_CREDENTIALS`
to the pinned server's realm/client/secret format and initialises a persistent
PostgreSQL realm. Restarting services preserves that realm and its credentials.
The API bootstrap creates writer and reader principals and atomically saves
their issued credentials in `.phlo/polaris-principals.json` with mode `0600`.
Both PyIceberg and the Trino adapter prefer these issued credentials over
configured client secrets. Bootstrap refuses to replace saved credentials;
restore the matching realm if its persistent database is lost.

Existing in-memory projects require an operator-managed realm and credential
migration before adopting PostgreSQL persistence. Phlo does not discard their
saved principal file or silently recreate those principals during the upgrade.

The Trino adapter generates the catalog configuration explicitly; install that
configuration in Trino's catalog directory before starting the query engine.
Treat the generated properties as a secret: it contains the writer credential.
Do not commit it or make it readable to other users.

Snapshot WAP additionally requires `wap.strategy: snapshot` in `phlo.yaml`
alongside `wap.enabled: true`.

Snapshot publication uses the shared Phlo PostgreSQL database to serialize
publishers across destination writes and the Iceberg release-ledger commit.
All writers for a warehouse must use the same PostgreSQL database. The ledger
commit also checks the exact Iceberg snapshot read before publication. A
candidate retains its original release revision; after another release wins,
stage and audit a new run rather than rebasing the old candidate.
An interrupted publication retains its candidate snapshots and durable intent;
retry that release to finish it before publishing another run. Retention cleanup
does not delete candidates belonging to an unfinished publication.

For consistent multi-table reads, resolve snapshots through the release
records. Individual table main snapshots are updated separately; ordinary
latest-table reads do not provide multi-table atomicity.

## Usage

```bash
# Start Polaris and bootstrap the catalog + principals
phlo services start
phlo polaris bootstrap

# Health and registered catalogs
phlo polaris status

# Inventory a Nessie project and register its tables in Polaris (dry run)
phlo polaris migrate-from-nessie
phlo polaris migrate-from-nessie --confirm
```

Trino and PyIceberg authenticate through the REST catalog with the writer
principal and OAuth2; Trino uses Polaris credential vending so no static S3
keys are embedded in query-engine configuration.
