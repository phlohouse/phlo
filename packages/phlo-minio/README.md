# phlo-minio

MinIO S3-compatible object storage plugin for Phlo.

## Description

Provides S3-compatible object storage for the data lake. Stores Iceberg table data, staging files, and backups.

## Container image

The server and bucket setup use `ghcr.io/phlohouse/phlo-minio:0.17.0`.
One image contains both MinIO and `mc`, built from checksum-verified upstream
source archives. New projects use `/bitnami/minio/data` and UID 1001.
Older projects can retain `/data`; migrate their volume ownership before
switching from a root-running upstream image.

The pinned server release is `RELEASE.2025-10-15T17-29-55Z`, which fixes
[the upstream session-policy bypass](https://github.com/minio/minio/security/advisories/GHSA-jjjj-jwhf-8rgr).
The client release is `RELEASE.2025-08-13T08-35-41Z`.
These pins and their SHA-256 checksums live in `src/phlo_minio/Dockerfile`.
Review upstream releases and security advisories before changing the pins.
The build also pins dependency updates for vulnerabilities in those releases.
These are Phlo builds, not official upstream binaries. Phlo must maintain
the dependency pins and runtime compatibility while it distributes this image.

MinIO image versions are independent of both Phlo and the Python plugin.
The initial image version is `0.17.0`. An image-only fix can advance to
`0.17.1` without releasing either Python distribution. Published tags are
immutable. Phlo release staging consumes the published MinIO image by digest
as a provider dependency and does not rebuild or promote it.

### Publish an image-only fix

1. Update the upstream source pins, checksums, or dependency fixes in the Dockerfile.
2. Advance the MinIO image version in both service YAML files, both support
   manifests, and the matching documentation and integration fixtures.
   Find the pins with `git grep 'ghcr.io/phlohouse/phlo-minio:'`.
   Do not change the Phlo or Python plugin version for an image-only fix.
3. Run the MinIO integration tests and merge the reviewed changes to `main`.
4. Dispatch the independent publisher:

   ```bash
   gh workflow run publish-minio.yml --repo phlohouse/phlo --ref main
   ```

The publisher builds native amd64 and arm64 images, scans each immutable
architecture digest, and creates the versioned multi-platform manifest only
when both pass. It reuses the service-image build jobs, retains attestations
and scan reports, and does not publish to PyPI or require whole-release
acceptance runs. The first publication also requires making the GHCR package
public before consumers can pull anonymously.

Nightly rescans cover the latest independently published MinIO image even
before the next Phlo release, alongside the latest Phlo release's pinned fleet.
Registry and scanner failures still fail the rescan.

### Publish the plugin and blueprint independently

The initial rollout requires publishing `phlo-minio` 0.16.1, then
`phlo-retail-files` 0.1.1. Neither requires a Phlo core release. After this
change merges to `main`, dispatch each separately and wait for the first
run to succeed before starting the second:

```bash
gh workflow run publish-storage-plugins.yml --repo phlohouse/phlo --ref main -f package=phlo-minio
gh workflow run publish-storage-plugins.yml --repo phlohouse/phlo --ref main -f package=phlo-retail-files
```

The publisher validates metadata, runs focused tests, builds the selected
distribution once, and installs its wheel against published dependencies
in a clean environment. MinIO publication also tests the existing-volume
migration with Docker. A separate job publishes those tested bytes using
the existing `PYPI_API_TOKEN` in the `release` environment. The token must
permit publishing the selected PyPI project. No reviewer protection is
required, and dispatching the image publisher does not dispatch this workflow.
Existing PyPI versions cannot be overwritten; advance the package version
for a subsequent plugin fix. The blueprint's clean install requires its
pinned MinIO plugin to have been published first.

### Create a lakehouse with the published image

The GHCR image and Python plugin are separate publications. The following
commands require `phlo-minio` 0.16.1 or later to be available on PyPI.
Publishing the container alone does not update an installed plugin.

In a new lakehouse project, install the plugin explicitly:

```bash
uv add 'phlo[defaults]==0.17.0' 'phlo-minio>=0.16.1,<0.17'
uv run phlo services init
uv run phlo services start --service minio --service minio-setup
```

The plugin generates GHCR image references for both services and prepares
the named volume for UID 1001. The Retail Files blueprint also pins this
plugin version. Existing example lockfiles still need the upgrade below;
do not assume a frozen lock selects the latest plugin.

### Migrate an existing lakehouse

Back up object storage and pause all lakehouse writers before applying this
migration. Do not run `services init --force`, delete a volume, or change the
Compose project name during the upgrade.

1. Update the plugin dependency and lockfile:

   ```bash
   uv add 'phlo-minio>=0.16.1,<0.17'
   uv sync
   ```

2. Preview the selected image, named volume, and existing data directory:

   ```bash
   uv run phlo minio migrate-image
   ```

3. After backing up storage and pausing writers, apply the migration:

   ```bash
   uv run phlo minio migrate-image --apply
   ```

The command keeps the data directory, volume identity, credentials, ports,
and bucket setup. It backs up the base Compose file, pulls both images,
stops MinIO, changes volume ownership to UID 1001, and restarts the server.
It then waits for readiness and runs bucket setup. It does not regenerate
other services or remove volumes. If the pull fails, it restores the original
Compose file before stopping any service.

The migration accepts writable named volumes mounted at `/data` or
`/bitnami/minio/data`. Reconcile MinIO image, storage, or command overrides
before running it. Automatic migration requires Docker Compose v2. Podman,
bind mounts, and distributed server layouts require a deployment-specific
migration. Production still uses the normal mutation
authorization policy; this command does not add a development override.

Read a known existing object and write a test object before resuming writers.
The backup `.phlo/docker-compose.pre-minio-image.yml` is private and may
contain secrets. Do not commit it. After a failure, inspect the server logs
and fix the deployment before retrying. Restoring the Compose backup does
not reverse ownership changes or an upstream data-format upgrade; restore
your storage backup if you need to roll back.

### Use an image-only fix without upgrading Phlo

After migrating ownership, an existing development project can set both images in
`.phlo/overrides/compose.yaml`. Replace `<published-image-version>` with the
newly published version:

```yaml
services:
  minio:
    image: ghcr.io/phlohouse/phlo-minio:<published-image-version>
  minio-setup:
    image: ghcr.io/phlohouse/phlo-minio:<published-image-version>
```

Then run `phlo services start --service minio --service minio-setup` without
`--build`. Existing projects retain their pinned image until you choose an
upgrade. The override preserves the data volume, health checks, and bucket
setup. Phlo rejects development Compose layers in production and staging.
For those environments, update the image references in your reviewed,
deployment-managed Compose configuration instead.

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
