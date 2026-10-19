# Provision an optional Observatory preview coordinator

Use `phlo trino preview provision` to generate a separate Trino 483 deployment.
The command never starts services or changes the ingestion coordinator, its
catalogs, or its budgets. Generated files are configuration evidence, not proof
that deployment enforcement works. Keep production security readiness checks.

## Configure the inputs

1. Add `preview` only to the environments that need query access in `phlo.yaml`.
   Environment IDs are configuration-defined. No second target is required.

   ```yaml
   environments:
     analytics:
       dagster_location: analytics
       nessie_ref: release_42
       preview:
         catalog: preview_analytics
         catalog_file: config/preview-analytics.properties
         password_file: /run/secrets/preview-analytics-password
   default_environment: analytics
   ```

   Target file paths resolve relative to the project directory. Use distinct
   catalog names and passwords. The generated identity is
   `phlo_api_preview_<environment-id>` and can read only its own catalog.
   The parent target supplies the ref. Never select a ref through SQL.

2. Supply an approved Iceberg REST catalog properties file. Its
   `iceberg.rest-catalog.prefix` must equal the parent `nessie_ref` exactly.
   Set the approved REST endpoint, warehouse, storage endpoint, authentication,
   and storage credentials. The generator forces `iceberg.security=READ_ONLY`.
   It rejects duplicate properties and Java property escapes rather than
   interpreting ambiguous input. Do not put credentials in version control.

3. Supply a trusted PEM CA bundle and a JKS or PKCS12 server keystore whose
   certificate covers `trino-preview`. Use private password files with mode
   `0600` or stricter and at least 16 characters. The keystore password accepts
   alphanumeric characters plus `.`, `_`, and `-`. Import generated credentials
   into your approved secret store or mount the private files. The command
   does not modify an external secret store.

4. Create a provider input file, such as `preview.json`:

   ```json
   {
     "ca_file": "/run/secrets/preview-ca.pem",
     "keystore_file": "/run/secrets/preview.p12",
     "keystore_password_file": "/run/secrets/preview-keystore-password",
     "network": "approved_phlo_network",
     "port": 10025
   }
   ```

   These paths resolve relative to the input file. The network must already
   exist and permit access to the approved catalog and storage endpoints.

## Generate and inspect the deployment

From the project directory, run:

```bash
phlo trino preview provision --config preview.json --output .phlo/preview-new
docker compose -p phlo-preview -f .phlo/preview-new/compose.yaml config --quiet
```

The output directory must not exist. The command creates private directories
and mode `0600` files, including catalog credentials, a PBKDF2 password database,
the server keystore, an authenticated CA-verifying health check, and
`api-settings.json`. Preserve file privacy when making files readable by the
Trino container UID. Never make secrets world-readable to repair a mount.

Review the deployment through your normal approval process before starting it:

```bash
docker compose -p phlo-preview -f .phlo/preview-new/compose.yaml up -d
```

The service publishes HTTPS only on loopback port `10025` by default. Internal
HTTP discovery remains private. The API must reach `https://trino-preview:8443`
on the selected network. Adapt the output CA and credential paths to the API's
mount namespace when applying `api-settings.json`. Do not replace `TRINO_URL`.
Only `PHLO_V1_PREVIEW_TRINO_URL` selects the preview endpoint.
`PHLO_V1_PREVIEW_TRINO_CA_FILE` selects its trusted CA bundle, otherwise platform
trust applies. Certificate verification cannot be disabled.

The API reads catalogs and credential paths from canonical `phlo.yaml` preview
stanzas. Generated `PHLO_V1_PREVIEW_CATALOGS` and
`PHLO_V1_PREVIEW_TRINO_PASSWORD_FILES` are runtime overrides for mounted output.
Their keys must match the opted-in targets exactly and refs must match the
parent targets. Legacy complete environment maps without preview stanzas retain
the `prod` and `staging` password variables for compatibility only.

## Verify before asserting deployment controls

Run `python -m phlo_api.preview_preflight --env analytics` inside the API configuration
context with `phlo-api` installed. It collects missing or invalid prerequisites
and reports setup guidance together. An intentionally unconfigured preview is
disabled. A valid local configuration remains `enforcement_unverified`.
The aggregate readiness and Query setup UI consume the same assessment.

Verify one API replica and one worker in the actual deployment before setting
`PHLO_V1_QUERY_SINGLE_REPLICA=1` and `PHLO_V1_QUERY_SINGLE_PROCESS=1` in the process
environment. `WEB_CONCURRENCY` must equal `1`. Verify the installed coordinator
limits before setting `PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED=1`.
Generation intentionally sets none of these assertions.

Verify catalog browsing and a real table read, completed query evidence in the
configured PostgreSQL evidence store, rejection of DDL and DML, cross-catalog
denial, and rejection of system and catalog session overrides. Verify row caps,
timeouts, cancellation, and concurrent admission. A successful health check or
`SELECT 1` alone does not prove those controls.

Active sessions live in one API process and do not survive API restart.
Completed evidence can be recovered only when the evidence database and its
schema are configured. Do not claim durable active-session recovery or
multi-worker support. Keep the explicit schema migration process for existing
deployments; generation does not migrate a database.

For password or certificate renewal, generate a new directory with the renewed
inputs. Review and replace the server configuration through the approved
deployment process, then update the API's mounted credentials and trust bundle.
The API rereads password files and CA trust on new queries. Verify new
credentials succeed and revoked credentials fail after server reload or restart.
Verify hostname and CA rejection as well as a successful renewed connection.
Retain the previous approved deployment for rollback. Never use `curl -k`.

## Understand the enforcement limits

The coordinator sets `query.max-run-time=20s`, `query.max-planning-time=5s`, and
`query.max-scan-physical-bytes=256MB`. System access control denies preview
identities all session-property overrides, and the API sends none. In Trino 483,
session-property manager defaults undergo the same authorization check as user
overrides. Combining manager defaults with that denial rejects ordinary queries.
Server-level defaults avoid this conflict. See Trino's
[session property manager documentation](https://trino.io/docs/current/admin/session-property-managers.html).

Trino checks elapsed time and reported physical scan bytes periodically. These
limits do not guarantee a byte-exact object-store or Iceberg-manifest cutoff.
The resource-group
`hardPhysicalDataScanLimit` is a per-quota-period admission/queueing quota, not a
strict per-query object-store or Iceberg-manifest byte ceiling. Trino does not
provide a hard kill guarantee for that quota once a query is running. The API
must retain its own timeout, cancellation, row, and response-byte limits. This
bundle does not make arbitrary ad-hoc Trino access safe, and is not active by
default.

`iceberg_preview_prod.properties` and
`iceberg_preview_staging.properties` are templates, not live environment
assertions. They mirror the package's current Nessie REST endpoint, warehouse,
and MinIO S3 settings; replace endpoint, warehouse, storage, and authentication
properties with the deployment's approved values while preserving the pinned
ref. The `prefix` values follow this repository's existing REST-catalog
convention and must be verified against the deployed Nessie REST extension.
