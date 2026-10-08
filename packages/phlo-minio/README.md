# phlo-minio

MinIO S3-compatible object storage plugin for Phlo.

## Description

Provides S3-compatible object storage for the data lake. Stores Iceberg table data, staging files, and backups.

## Container image

The server and bucket setup use `ghcr.io/phlohouse/phlo-minio:0.17.0`.
One image contains both MinIO and `mc`, built from checksum-verified upstream
source archives. It preserves `/bitnami/minio/data` and runs as UID 1001,
so existing named volumes keep the same mount path and ownership.

The pinned server release is `RELEASE.2025-10-15T17-29-55Z`, which fixes
[the upstream session-policy bypass](https://github.com/minio/minio/security/advisories/GHSA-jjjj-jwhf-8rgr).
The client release is `RELEASE.2025-08-13T08-35-41Z`.
These pins and their SHA-256 checksums live in `src/phlo_minio/Dockerfile`.
Review upstream releases and security advisories before changing the pins.
The build also pins dependency updates for vulnerabilities in those releases.
These are Phlo builds, not official upstream binaries. Phlo must maintain
the dependency pins and runtime compatibility while it distributes this image.

The existing staged-service workflow builds native amd64 and arm64 images,
scans each digest, and publishes a multi-platform development image. Release
promotion publishes the verified image without rebuilding it and records its
digest in the release bill of materials. The first release must publish the
image and make the GHCR package public before consumers can pull anonymously.
Image tags follow the core Phlo release version, not the independently versioned
Python plugin. Nightly rescans use the latest published release's image inventory
with the current scan policy, so unreleased images do not break the rescan.

Before publication, or to rebuild locally, use:

```bash
phlo services start --service minio --build
```

MinIO and `mc` are AGPL-3.0-or-later, independently of the plugin's MIT licence.
The image includes licences and credits in `/usr/share/licenses/minio`, plus
corresponding source archives with vendored dependencies and the build recipe
in `/usr/share/minio-source`. Preserve these files when redistributing the
image. Review the AGPL obligations for your deployment, particularly if you
modify MinIO or distribute a derived image.

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
