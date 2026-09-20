# phlo-minio

MinIO S3-compatible object storage plugin for Phlo.

## Description

Provides S3-compatible object storage for the data lake. Stores Iceberg table data, staging files, and backups.

## Installation

```bash
pip install phlo-minio
# or
phlo plugin install minio
```

## Configuration

| Variable                | Default    | Description              |
| ----------------------- | ---------- | ------------------------ |
| `MINIO_ROOT_USER`       | `minio`    | Root username            |
| `MINIO_ROOT_PASSWORD`   | `minio123` | Root password            |
| `MINIO_API_PORT`        | `10001`    | S3 API host port         |
| `MINIO_CONSOLE_PORT`    | `10002`    | Web console host port    |
| `MINIO_SERVER_URL`      | -          | TLS server URL           |
| `MINIO_OIDC_CONFIG_URL` | -          | OIDC provider config URL |
| `MINIO_AUTO_ENCRYPTION` | `off`      | Auto-encryption mode     |
| `MINIO_AUDIT_ENABLED`   | `off`      | Audit webhook delivery   |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature                 | How It Works                                         |
| ----------------------- | ---------------------------------------------------- |
| **Metrics Labels**      | Exposes MinIO metrics at `/minio/v2/metrics/cluster` |
| **Prometheus Scraping** | Auto-scraped by Prometheus via Docker labels         |
| **Volume Mounting**     | Persists data to the `minio-data` Docker volume      |

### Metrics Labels

```yaml
compose:
  labels:
    phlo.metrics.enabled: "true"
    phlo.metrics.port: "minio:9000"
    phlo.metrics.path: "/minio/v2/metrics/cluster"
```

## Usage

```bash
phlo services start --service minio
```

## Audit Logging

The bundled MinIO service exposes Phlo's supported storage audit-log path:

```bash
MINIO_AUDIT_ENABLED=on
MINIO_AUDIT_ENDPOINT=http://loki:3100/loki/api/v1/push
```

Route audit events to a durable backend and correlate them with centralized application logs. See `docs/guides/monitor-and-debug.md` for the full platform posture.

## Endpoints

- **S3 API**: `http://localhost:10001`
- **Console**: `http://localhost:10002`

## Entry Points

- `phlo.plugins.services` - Provides `MinioServicePlugin`
