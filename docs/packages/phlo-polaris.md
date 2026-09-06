# phlo-polaris

Apache Polaris catalog service plugin for Phlo.

## Overview

`phlo-polaris` is a catalog **alternative** to `phlo-nessie`: a pinned Apache
Polaris Iceberg REST catalog with OAuth/RBAC and credential vending, backed
by Phlo PostgreSQL and MinIO/RustFS. It implements Phlo's snapshot-promotion
WAP strategy — runs stage immutable candidate Iceberg snapshots, audits those
exact snapshots, and publishes them by advancing a compare-and-swap-guarded
release pointer. A project selects one catalog per warehouse
(`catalog: nessie` or `catalog: polaris`); both cannot be the default writer.

### Key features

- Digest-pinned `apache/polaris` service with health checks and metrics
- Bootstrap hook creating the Phlo catalog, writer/reader principals, and grants
- `SnapshotPromotionCatalog` capability (candidate refs, CAS release pointer, abort/retention)
- Trino catalog properties with OAuth2 and credential vending; identical PyIceberg configuration
- `phlo polaris migrate-from-nessie`: dry-run by default, never deletes Nessie metadata or data
- Release-ledger backup contribution and security readiness inspection

## Installation

```bash
pip install phlo-polaris
```

## Configuration

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `POLARIS_PORT` | `10018` | Polaris API host port |
| `POLARIS_ROOT_CREDENTIALS` | `root:s3cr3t` | Bootstrap principal credentials (secret) |
| `POLARIS_WRITER_CLIENT_ID` | `phlo_writer` | Writer principal client id |
| `POLARIS_WRITER_CLIENT_SECRET` | `phlo-writer-secret` | Writer principal secret (secret) |
| `POLARIS_READER_CLIENT_ID` | `phlo_reader` | Reader principal client id |
| `POLARIS_READER_CLIENT_SECRET` | `phlo-reader-secret` | Reader principal secret (secret) |

Snapshot WAP additionally requires `wap.enabled: true` with
`wap.strategy: snapshot` in `phlo.yaml`.

## Usage

```bash
phlo services start
phlo polaris bootstrap
phlo polaris status
phlo polaris migrate-from-nessie [--confirm]
```
